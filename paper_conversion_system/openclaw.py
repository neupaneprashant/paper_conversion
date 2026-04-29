"""OpenClaw integration: OAuth2 client, completion API, and LLM-powered agent sessions.

Usage (client credentials, server-to-server):
    config = OpenClawConfig.from_env()
    if config:
        provider = OpenClawLLMProvider(config)
        # Pass to route_and_run via llm_provider= or openclaw_client=

Environment variables:
    OPENCLAW_BASE_URL        Base URL of the OpenClaw API (e.g. https://api.openclaw.io)
    OPENCLAW_CLIENT_ID       OAuth2 client_id
    OPENCLAW_CLIENT_SECRET   OAuth2 client_secret
    OPENCLAW_MODEL           Model name (default: codex)
    OPENCLAW_TOKEN_URL       Token endpoint (default: {base_url}/oauth/token)
    OPENCLAW_COMPLETION_URL  Completion endpoint (default: {base_url}/v1/messages)
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse
import urllib.request
import urllib.error

from .models import CanonicalPaperRepresentation, ConversionReport
from .orchestrator import LLMContextProvider


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class OpenClawConfig:
    base_url: str
    client_id: str
    client_secret: str
    model: str = "codex"
    token_url: str = ""        # defaults to {base_url}/oauth/token
    completion_url: str = ""   # defaults to {base_url}/v1/messages
    authorize_url: str = ""    # defaults to {base_url}/oauth/authorize
    token_path: Path = field(default_factory=lambda: Path.home() / ".openclaw" / "token.json")
    # Optional: override scopes
    scopes: str = "agents:write completions:write"

    def __post_init__(self) -> None:
        base = self.base_url.rstrip("/")
        if not self.token_url:
            self.token_url = f"{base}/oauth/token"
        if not self.completion_url:
            self.completion_url = f"{base}/v1/messages"
        if not self.authorize_url:
            self.authorize_url = f"{base}/oauth/authorize"

    @classmethod
    def from_env(cls) -> "OpenClawConfig | None":
        base_url = os.environ.get("OPENCLAW_BASE_URL", "").strip()
        client_id = os.environ.get("OPENCLAW_CLIENT_ID", "").strip()
        client_secret = os.environ.get("OPENCLAW_CLIENT_SECRET", "").strip()
        if not (base_url and client_id and client_secret):
            return None
        return cls(
            base_url=base_url,
            client_id=client_id,
            client_secret=client_secret,
            model=os.environ.get("OPENCLAW_MODEL", "codex"),
            token_url=os.environ.get("OPENCLAW_TOKEN_URL", ""),
            completion_url=os.environ.get("OPENCLAW_COMPLETION_URL", ""),
        )

    @classmethod
    def from_file(cls, path: Path) -> "OpenClawConfig | None":
        """Load config from a JSON file (used by the API server to persist settings)."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "client_id": self.client_id,
            "client_secret": "***",  # never expose in API responses
            "model": self.model,
            "token_url": self.token_url,
            "completion_url": self.completion_url,
            "authorize_url": self.authorize_url,
        }


# ---------------------------------------------------------------------------
# OAuth2 token management
# ---------------------------------------------------------------------------

class OpenClawOAuthClient:
    """OAuth2 token lifecycle manager.

    Supports:
    - client_credentials flow (server-to-server; no user interaction)
    - refresh_token flow (if a refresh token is available from a prior auth-code exchange)

    Tokens are cached on disk at config.token_path so restarts don't require re-auth.
    """

    def __init__(self, config: OpenClawConfig) -> None:
        self.config = config
        self._token: dict[str, Any] = {}
        self._load_token()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_access_token(self) -> str:
        # OpenClaw Codex: check if the attached token is still valid.
        static = getattr(self.config, "_static_token", None)
        if static:
            expires_at = getattr(self.config, "_expires_at", 0)
            if time.time() < expires_at - 60:
                return static
            # Token expired — try to refresh using the stored refresh token.
            refresh = getattr(self.config, "_refresh_token", "")
            if refresh:
                try:
                    new_token = self._refresh_codex_token(refresh)
                    self.config._static_token = new_token  # type: ignore[attr-defined]
                    # Update the stored profiles so next run also works.
                    self._persist_refreshed_token(new_token)
                    return new_token
                except Exception:
                    pass
            # Refresh failed but token may still work — return it and let the
            # API call fail with a clear HTTP error if truly expired.
            return static
        if self._is_expired():
            self._refresh_or_acquire()
        return self._token["access_token"]

    def _refresh_codex_token(self, refresh_token: str) -> str:
        """Use the stored refresh token to get a new OpenAI access token."""
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.client_id,
        }
        data = self._post_token(payload)
        new_access = data.get("access_token", "")
        if not new_access:
            raise RuntimeError("Refresh response missing access_token")
        # Update expiry on config so subsequent calls use the right window.
        expires_in = int(data.get("expires_in", 3600))
        self.config._expires_at = time.time() + expires_in  # type: ignore[attr-defined]
        # Persist new refresh token if one was returned.
        if data.get("refresh_token"):
            self.config._refresh_token = data["refresh_token"]  # type: ignore[attr-defined]
        return new_access

    def _persist_refreshed_token(self, new_access: str) -> None:
        """Write the new access token back to auth-profiles.json."""
        try:
            path = _AUTH_PROFILES_PATH
            raw = json.loads(path.read_text(encoding="utf-8"))
            for key, profile in raw.get("profiles", {}).items():
                if key.startswith("openai-codex:"):
                    profile["access"] = new_access
                    expires_at = getattr(self.config, "_expires_at", 0)
                    profile["expires"] = int(expires_at * 1000)
                    rt = getattr(self.config, "_refresh_token", "")
                    if rt:
                        profile["refresh"] = rt
            path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        except Exception:
            pass  # Best-effort persistence — don't crash the conversion

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> None:
        """Exchange an authorization code for tokens (auth-code + PKCE flow)."""
        payload: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        self._token = self._post_token(payload)
        self._store_token()

    def build_authorize_url(self, redirect_uri: str, state: str, code_challenge: str = "") -> str:
        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.config.scopes,
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{self.config.authorize_url}?{urlencode(params)}"

    def clear(self) -> None:
        self._token = {}
        try:
            self.config.token_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_expired(self) -> bool:
        if not self._token.get("access_token"):
            return True
        return time.time() >= self._token.get("expires_at", 0) - 60

    def _refresh_or_acquire(self) -> None:
        if self._token.get("refresh_token"):
            try:
                self._do_refresh()
                return
            except Exception:
                pass
        self._do_client_credentials()

    def _do_client_credentials(self) -> None:
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "scope": self.config.scopes,
        }
        self._token = self._post_token(payload)
        self._store_token()

    def _do_refresh(self) -> None:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._token["refresh_token"],
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        self._token = self._post_token(payload)
        self._store_token()

    def _post_token(self, payload: dict[str, str]) -> dict[str, Any]:
        body = urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            self.config.token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            raise RuntimeError(
                f"OpenClaw token request failed ({exc.code}): {body_bytes.decode('utf-8', errors='replace')}"
            ) from exc
        if "error" in data:
            raise RuntimeError(f"OpenClaw OAuth error: {data['error']} — {data.get('error_description', '')}")
        data["expires_at"] = time.time() + int(data.get("expires_in", 3600))
        return data

    def _load_token(self) -> None:
        try:
            self._token = json.loads(self.config.token_path.read_text(encoding="utf-8"))
        except Exception:
            self._token = {}

    def _store_token(self) -> None:
        try:
            self.config.token_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.token_path.write_text(
                json.dumps(self._token, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# HTTP completion client
# ---------------------------------------------------------------------------

class OpenClawClient:
    """Thin HTTP client for the OpenClaw completion API.

    Assumes an Anthropic-compatible Messages API shape:
        POST {completion_url}
        { "model": "...", "system": "...", "messages": [...], "max_tokens": N }
    Response: { "content": [{ "type": "text", "text": "..." }] }

    If OpenClaw uses a different shape, subclass and override _parse_response.
    """

    def __init__(self, config: OpenClawConfig) -> None:
        self.config = config
        self._auth = OpenClawOAuthClient(config)

    @property
    def oauth(self) -> OpenClawOAuthClient:
        return self._auth

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 12000,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """Send a single-turn completion request and return the response text."""
        codex_backend = "chatgpt.com/backend-api" in self.config.completion_url
        chat_completions = "chat/completions" in self.config.completion_url
        responses_api = "/v1/responses" in self.config.completion_url

        token = self._auth.get_access_token()

        if codex_backend:
            # OpenClaw Codex via chatgpt.com/backend-api/codex/responses.
            # Requires SSE streaming, chatgpt-account-id header, and originator: pi.
            account_id = getattr(self.config, "_account_id", "")
            payload: dict[str, Any] = {
                "model": self.config.model,
                "store": False,
                "stream": True,
                "instructions": system,
                "input": [{"role": "user", "content": user}],
                "text": {"verbosity": "medium"},
                "include": ["reasoning.encrypted_content"],
                "tool_choice": "auto",
                "parallel_tool_calls": True,
            }
            if extra:
                payload.update(extra)
            headers: dict[str, str] = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "OpenAI-Beta": "responses=experimental",
                "accept": "text/event-stream",
                "originator": "pi",
            }
            if account_id:
                headers["chatgpt-account-id"] = account_id
            return self._complete_codex_sse(self.config.completion_url, headers, payload)

        elif chat_completions:
            payload = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": max_tokens,
            }
        elif responses_api:
            payload = {
                "model": self.config.model,
                "instructions": system,
                "input": user,
                "max_output_tokens": max_tokens,
            }
        else:
            # Anthropic Messages API (default)
            payload = {
                "model": self.config.model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": max_tokens,
            }
        if extra:
            payload.update(extra)

        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        if "anthropic.com" in self.config.completion_url:
            headers["anthropic-version"] = "2023-06-01"
        req = urllib.request.Request(
            self.config.completion_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            raise RuntimeError(
                f"OpenClaw completion request failed ({exc.code}): "
                f"{body_bytes.decode('utf-8', errors='replace')}"
            ) from exc
        return self._parse_response(data)

    def _complete_codex_sse(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> str:
        """Make a streaming SSE request to the Codex backend and return the text."""
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        text_deltas: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                buffer = b""
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    buffer += chunk
                    # Process complete SSE lines (separated by \n\n blocks).
                    while b"\n\n" in buffer:
                        block, buffer = buffer.split(b"\n\n", 1)
                        for raw_line in block.split(b"\n"):
                            line = raw_line.decode("utf-8", errors="replace").strip()
                            if not line.startswith("data:"):
                                continue
                            data_str = line[5:].strip()
                            if not data_str or data_str == "[DONE]":
                                continue
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            result = self._handle_sse_event(event, text_deltas)
                            if result is not None:
                                return result
        except urllib.error.HTTPError as exc:
            body_bytes = exc.read()
            raise RuntimeError(
                f"OpenClaw completion request failed ({exc.code}): "
                f"{body_bytes.decode('utf-8', errors='replace')}"
            ) from exc

        if text_deltas:
            return "".join(text_deltas)
        raise RuntimeError("No text extracted from Codex SSE stream")

    def _handle_sse_event(
        self,
        event: dict[str, Any],
        text_deltas: list[str],
    ) -> str | None:
        """Process a single SSE event. Returns text if stream is complete, else None."""
        event_type = event.get("type", "")

        if event_type == "response.output_text.delta":
            delta = event.get("delta", "")
            if isinstance(delta, str):
                text_deltas.append(delta)

        elif event_type in ("response.done", "response.completed", "response.incomplete"):
            # Try to pull full text from the terminal response object.
            resp_obj = event.get("response", {})
            if resp_obj:
                text = self._extract_from_response_obj(resp_obj)
                if text:
                    return text
            # Fall back to accumulated deltas.
            if text_deltas:
                return "".join(text_deltas)
            raise RuntimeError("Codex stream ended without text output")

        elif event_type == "response.failed":
            error_msg = (
                event.get("response", {}).get("error", {}).get("message", "")
                or "Codex response failed"
            )
            raise RuntimeError(f"Codex response failed: {error_msg}")

        elif event_type == "error":
            raise RuntimeError(
                f"Codex error: {event.get('message') or event.get('code') or str(event)}"
            )

        return None

    def _extract_from_response_obj(self, resp_obj: dict[str, Any]) -> str:
        """Pull text out of a Responses API terminal response object."""
        for item in resp_obj.get("output", []):
            if not isinstance(item, dict):
                continue
            for block in item.get("content", []):
                if isinstance(block, dict) and block.get("type") in ("output_text", "text"):
                    text = block.get("text", "")
                    if text:
                        return text
            # Fallback: item-level text field.
            if isinstance(item.get("text"), str) and item["text"]:
                return item["text"]
        return ""

    def ping(self) -> bool:
        """Check if a token is available (does not make a live API call)."""
        try:
            token = self._auth.get_access_token()
            return bool(token)
        except Exception:
            return False

    def _parse_response(self, data: dict[str, Any]) -> str:
        # OpenAI Responses API shape (used by OpenClaw Codex)
        # { "output": [{ "type": "message", "content": [{ "type": "output_text", "text": "..." }] }] }
        if "output" in data and isinstance(data["output"], list):
            for item in data["output"]:
                if isinstance(item, dict) and item.get("type") == "message":
                    for block in item.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "output_text":
                            return block["text"]
            # Fallback: grab first text anywhere in output
            for item in data["output"]:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content")
                    if isinstance(text, str):
                        return text
        # Anthropic Messages API shape
        if "content" in data and isinstance(data["content"], list):
            for block in data["content"]:
                if isinstance(block, dict) and block.get("type") == "text":
                    return block["text"]
        # OpenAI Chat Completions shape
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        # Plain text
        if "text" in data:
            return data["text"]
        raise RuntimeError(f"Unrecognised API response shape: {list(data.keys())}")


# ---------------------------------------------------------------------------
# Agent system prompts
# ---------------------------------------------------------------------------

# NOTE: These prompts are called AFTER the deterministic pre-processing pass
# (_preprocess_ieee_to_acm / _preprocess_acm_to_ieee) has already handled all
# 1:1 macro substitutions.  The LLM's job is the structurally complex remainder:
# author blocks, acknowledgment restructuring, preamble cleanup, and edge cases.

APRIL_SYSTEM = """\
You are April, a specialist LaTeX conversion agent with a single mission: convert
IEEE IEEEtran manuscripts into ACM acmart (sigconf) format.  You have years of
experience with both class files and know every macro, environment, and structural
difference by heart.

────────────────────────────────────────────────────────────────
WHAT HAS ALREADY BEEN DONE (pre-processor handled these — do NOT redo them):
• \\documentclass replaced with \\documentclass[sigconf]{acmart}
• \\IEEEPARstart{X}{yz} replaced with plain text Xyz
• \\begin{IEEEproof}/\\end{IEEEproof} replaced with proof environment
• \\begin{IEEEkeywords}/\\end{IEEEkeywords} replaced with \\keywords{…}
• \\bibliographystyle{IEEEtran} removed
• \\usepackage{graphicx} added if missing
• Float environments (%%FLOAT_NNN%% markers) stripped — you must NOT touch them
────────────────────────────────────────────────────────────────

YOUR TASKS — apply exactly these remaining structural changes:

━━ TASK 1 — Author block conversion (most important task) ━━

IEEE uses a single monolithic \\author{} block with \\IEEEauthorblockN/\\IEEEauthorblockA.
ACM uses one triplet per author: \\author / \\affiliation / \\email.

EXACT INPUT PATTERN (IEEE style):
  \\author{
    \\IEEEauthorblockN{Alice Smith, Bob Jones}
    \\IEEEauthorblockA{\\textit{School of Computing}\\\\
    MIT, Cambridge, MA\\\\
    alice@mit.edu, bob@mit.edu}
    \\and
    \\IEEEauthorblockN{Carol Williams}
    \\IEEEauthorblockA{\\textit{EECS Department}\\\\
    Stanford University, Stanford, CA\\\\
    carol@stanford.edu}
  }

EXACT OUTPUT PATTERN (ACM style — one triplet per named person):
  \\author{Alice Smith}
  \\affiliation{\\institution{School of Computing, MIT}\\city{Cambridge}\\state{MA}}
  \\email{alice@mit.edu}

  \\author{Bob Jones}
  \\affiliation{\\institution{School of Computing, MIT}\\city{Cambridge}\\state{MA}}
  \\email{bob@mit.edu}

  \\author{Carol Williams}
  \\affiliation{\\institution{EECS Department, Stanford University}\\city{Stanford}\\state{CA}}
  \\email{carol@stanford.edu}

RULES for author conversion:
• Split comma-separated names in \\IEEEauthorblockN into individual \\author{} calls.
• Each person gets the same \\affiliation{} from their \\IEEEauthorblockA block.
• Parse email addresses from \\IEEEauthorblockA text; assign one per author in order.
• Strip \\textit{} wrappers inside affiliation text.
• Strip \\\\ line breaks; use \\city{} and \\state{} when city/state are identifiable.
• If you cannot determine individual emails, use \\email{contact@institution.edu}.
• Remove \\thanks{…} from author names and move content to a \\footnotetext or discard.

━━ TASK 2 — Acknowledgment section ━━

If you see a raw \\section*{Acknowledgment} ... block (i.e. the pre-processor missed it
or it uses a variant spelling), wrap it:
  \\begin{acks}
  <body text>
  \\end{acks}
Remove the \\section*{Acknowledgment} line itself.

━━ TASK 3 — Preamble cleanup ━━

• Remove any remaining IEEE-specific packages: IEEEtrantools, cite (replace with natbib
  or leave absent — acmart has its own citation system).
• Remove \\IEEEoverridecommandlockouts, \\overrideIEEEmargins.
• If \\pagestyle{…} or \\thispagestyle{…} appear, remove them (acmart sets its own).
• If \\columnsep, \\columnwidth, \\textwidth overrides appear in the preamble, remove.
• Keep all \\newcommand / \\renewcommand / \\DeclareMathOperator definitions untouched.

━━ TASK 4 — CCS concepts block ━━

ACM requires CCS concepts.  If the source has none, add a minimal placeholder
immediately before \\maketitle:
  \\begin{CCSXML}
  <ccs2012>
   <concept><concept_id>10010147.10010178</concept_id>
    <concept_desc>Computing methodologies~Artificial intelligence</concept_desc>
    <concept_significance>500</concept_significance></concept>
  </ccs2012>
  \\end{CCSXML}
  \\ccsdesc[500]{Computing methodologies~Artificial intelligence}
Only add this if \\begin{CCSXML} does not already exist.

━━ TASK 5 — Remaining IEEEtran leakage ━━

After all other conversions, scan the entire document for any remaining IEEE-specific
macro usage.  Apply these final substitutions:
• \\IEEEauthorrefmark{N}  →  \\textsuperscript{N}
• \\IEEEeqnarraymulticol  →  remove
• \\begin{IEEEeqnarray}…\\end{IEEEeqnarray}  →  \\begin{align}…\\end{align}
• \\begin{IEEEeqnarray*}…\\end{IEEEeqnarray*}  →  \\begin{align*}…\\end{align*}

━━ FLOAT MARKERS ━━
Lines like  % %%FLOAT_001%%  are position markers.  Copy them verbatim to the
same relative position in your output.  Never delete or move them.

────────────────────────────────────────────────────────────────
ABSOLUTE PRESERVATION — never change these:
• Every mathematical equation (inline $…$, display \\[…\\], equation/align/gather envs)
• Every \\includegraphics{} call — path and options must be byte-for-byte identical
• Every \\cite{}, \\ref{}, \\label{} key
• Every figure/table environment body (caption, label, placement specifier)
• Every section heading and all body prose (word for word)
• Every \\newcommand / \\renewcommand / \\DeclareMathOperator definition
• The \\bibliography{} file argument
────────────────────────────────────────────────────────────────

OUTPUT: Return ONLY the complete converted LaTeX source.
No markdown fences, no explanations, no comments you added yourself.
Start the output with \\documentclass and end with \\end{document}.\
"""

FRIDAY_SYSTEM = """\
You are Friday, a specialist LaTeX conversion agent with a single mission: convert
ACM acmart manuscripts into IEEE IEEEtran (conference) format.  You know every
acmart macro and its IEEEtran equivalent, or know when to drop it cleanly.

────────────────────────────────────────────────────────────────
WHAT HAS ALREADY BEEN DONE (pre-processor handled these — do NOT redo them):
• \\documentclass replaced with \\documentclass[conference]{IEEEtran}
• \\keywords{…} replaced with \\begin{IEEEkeywords}…\\end{IEEEkeywords}
• \\begin{acks}/\\end{acks} replaced with \\section*{Acknowledgment}
• ACM-only metadata macros removed: \\ccsdesc, \\begin{CCSXML}…\\end{CCSXML},
  \\acmConference, \\acmDOI, \\acmISBN, \\copyrightyear, \\acmYear, \\acmPrice,
  \\acmSubmissionID, \\setcopyright, \\received, \\revised, \\accepted
• \\begin{teaserfigure}…\\end{teaserfigure} removed
• \\bibliographystyle{IEEEtran} added if bibliography present
• \\usepackage{graphicx} added if missing
• Float environments (%%FLOAT_NNN%% markers) stripped — do NOT touch them
────────────────────────────────────────────────────────────────

YOUR TASKS — apply exactly these remaining structural changes:

━━ TASK 1 — Author block conversion (most important task) ━━

ACM uses per-author triplets; IEEE uses a single \\author{} block with
\\IEEEauthorblockN (name) and \\IEEEauthorblockA (affiliation + email), separated
by \\and for each affiliation group.

EXACT INPUT PATTERN (ACM style — multiple triplets):
  \\author{Alice Smith}
  \\affiliation{\\institution{School of Computing}\\city{Cambridge}\\state{MA}\\country{USA}}
  \\email{alice@mit.edu}

  \\author{Bob Jones}
  \\affiliation{\\institution{School of Computing}\\city{Cambridge}\\state{MA}\\country{USA}}
  \\email{bob@mit.edu}

  \\author{Carol Williams}
  \\affiliation{\\institution{EECS Department, Stanford University}\\city{Stanford}\\state{CA}}
  \\email{carol@stanford.edu}

EXACT OUTPUT PATTERN (IEEE style — group by shared affiliation):
  \\author{
  \\IEEEauthorblockN{Alice Smith, Bob Jones}
  \\IEEEauthorblockA{\\textit{School of Computing}\\\\
  MIT, Cambridge, MA\\\\
  alice@mit.edu, bob@mit.edu}
  \\and
  \\IEEEauthorblockN{Carol Williams}
  \\IEEEauthorblockA{\\textit{EECS Department, Stanford University}\\\\
  Stanford, CA\\\\
  carol@stanford.edu}
  }

RULES for author conversion:
• Group authors that share the same \\institution{} into one \\IEEEauthorblockN (comma-sep names).
• Each affiliation group gets its own \\IEEEauthorblockA, separated from the next by \\and.
• Build the affiliation line: \\textit{institution}, city, state.  If country is USA, omit it.
• List emails comma-separated in the affiliation block, one per author in that group.
• Strip acmart affiliation sub-commands (\\city, \\state, \\country, \\postcode, \\streetaddress)
  and fold the values into plain text.
• Remove \\orcid{…} calls entirely (no IEEE equivalent).
• Remove \\authornotemark[…] calls.

━━ TASK 2 — Preamble cleanup ━━

• Remove acmart-specific packages: acmart loads many things internally.
  Remove explicit \\usepackage for: booktabs (keep if tables use it), microtype,
  hyperref (IEEEtran handles this), biblatex (switch to bibtex/IEEEtran).
• Remove \\setlength{\\bibitemsep}{…} and similar biblatex spacing commands.
• Remove \\addbibresource{…}; if present, add \\bibliography{<filename without ext>}.
• If \\printbibliography appears in the body, replace with \\bibliography{<resource>}
  (extract the filename from \\addbibresource or use "references" as fallback).
• Remove \\urlstyle{…}, \\hypersetup{…} (IEEEtran sets these).
• Keep all \\newcommand / \\renewcommand / \\DeclareMathOperator definitions untouched.

━━ TASK 3 — Abstract environment ━━

ACM wraps the abstract before \\maketitle; IEEE puts it after \\maketitle inside
\\begin{abstract}…\\end{abstract}.  Ensure:
• \\maketitle appears before the abstract in the output.
• The abstract is inside \\begin{abstract}…\\end{abstract} (not a \\section{}).

━━ TASK 4 — Remaining ACM leakage ━━

After all other conversions, scan for remaining ACM macros:
• \\begin{marginfigure}…\\end{marginfigure}  →  convert to \\begin{figure}…\\end{figure}
• \\begin{sidebar}…\\end{sidebar}  →  convert to a \\begin{quote}…\\end{quote} block
• \\footnotemark[…] / \\footnotetext[…]  →  plain \\footnote{…} where possible
• \\acmArticle, \\acmVolume, \\acmNumber, \\acmMonth  →  remove

━━ FLOAT MARKERS ━━
Lines like  % %%FLOAT_001%%  are position markers.  Copy them verbatim to the
same relative position in your output.  Never delete or move them.

────────────────────────────────────────────────────────────────
ABSOLUTE PRESERVATION — never change these:
• Every mathematical equation (inline $…$, display \\[…\\], equation/align/gather envs)
• Every \\includegraphics{} call — path and options must be byte-for-byte identical
• Every \\cite{}, \\ref{}, \\label{} key
• Every figure/table environment body (caption, label, placement specifier)
• Every section heading and all body prose (word for word)
• Every \\newcommand / \\renewcommand / \\DeclareMathOperator definition
• The \\bibliography{} file argument (only the filename, not the command itself)
────────────────────────────────────────────────────────────────

OUTPUT: Return ONLY the complete converted LaTeX source.
No markdown fences, no explanations, no comments you added yourself.
Start the output with \\documentclass and end with \\end{document}.\
"""

COMP_HARMONIZATION_SYSTEM = """\
You are Comp, the quality-assurance agent for academic LaTeX paper conversions.

Your task: review the converted LaTeX document below and fix any remaining issues.
Work silently — do not add commentary inside the LaTeX.

CHECK AND FIX:
1. Leaked source-venue macros (e.g. \\IEEEtran inside an ACM document or vice versa).
2. Duplicate \\begin{document} / \\end{document} pairs — keep only one of each.
3. Duplicate \\title{} declarations — keep only the first.
4. Broken figure/table environments (missing \\end{figure} etc.).
5. Any obviously malformed LaTeX command fragments at end-of-file.

DO NOT change:
• Any mathematical content.
• Any citation keys, reference labels, or cross-reference labels.
• Any body prose.

Output format — two sections separated by the literal marker line ###NOTES###:
  SECTION 1: the complete corrected LaTeX source (nothing else before this marker)
  SECTION 2: a bullet list of every change you made (or "No changes required")
\
"""

COMP_REPAIR_SYSTEM = """\
You are Comp, a LaTeX repair specialist. A LaTeX document failed to compile with
pdflatex. You will receive the full LaTeX source followed by the compile error log.
Your job is to fix the LaTeX so it compiles cleanly.

REPAIR RULES:
1. Read the error log carefully. Fix only what the errors describe.
2. Common errors and their fixes:
   • "Undefined control sequence \\X" — remove or replace \\X with the correct macro.
   • "Environment X undefined" — replace \\begin{X}...\\end{X} with the correct environment.
   • "Missing $ inserted" — wrap the offending math token in $...$.
   • "Missing \\begin{document}" — ensure \\begin{document} exists exactly once.
   • "\\end{X} on wrong level" or mismatched environments — find and close the open env.
   • "LaTeX Error: File 'X.sty' not found" — remove \\usepackage{X} from the preamble.
   • "Too many }'s" / "Missing {" — find and fix the brace mismatch near the error line.
   • "\\author already defined" — remove duplicate \\author{} calls.
   • "Option clash for package X" — remove the duplicate \\usepackage{X} with conflicting options.
3. NEVER change: math content, citation keys (\\cite{}), reference labels (\\ref{}, \\label{}),
   section headings, body prose, or \\includegraphics paths.
4. NEVER add \\newcommand for macros you don't recognise — remove the broken call instead.
5. If a float marker line appears (% [FIGURE PLACEHOLDER NNN] or \\typeout{FLOAT_MARKER_NNN}),
   preserve it exactly in place.

Output format — two sections separated by the literal marker line ###NOTES###:
  SECTION 1: the complete repaired LaTeX source
  SECTION 2: bullet list of every change made and which error it fixes
\
"""


# ---------------------------------------------------------------------------
# LLM-powered conversion agents
# ---------------------------------------------------------------------------

class OpenClawAgentSession:
    """A single-task LLM agent session backed by the OpenClaw codex model.

    Each conversion direction gets its own session with an appropriate system
    prompt.  The session is stateless between calls; each convert() call is
    an independent completion.
    """

    def __init__(self, client: OpenClawClient, name: str, system_prompt: str) -> None:
        self.client = client
        self.name = name
        self.system_prompt = system_prompt

    def convert(self, latex_source: str) -> str:
        """Send the LaTeX source to the model and return the converted source."""
        return self.client.complete(
            system=self.system_prompt,
            user=latex_source,
            max_tokens=32000,
        )

    def harmonize(self, latex_source: str) -> tuple[str, list[str]]:
        """Send source to Comp for harmonization; return (corrected_source, notes)."""
        raw = self.client.complete(
            system=self.system_prompt,
            user=latex_source,
            max_tokens=32000,
        )
        return _split_comp_response(raw)

    def repair_latex(self, latex_source: str, error_log: str) -> tuple[str, list[str]]:
        """Send broken LaTeX + compile errors to the repair agent; return (fixed_source, notes)."""
        user_msg = (
            "=== LaTeX Source ===\n"
            + latex_source
            + "\n\n=== Compile Error Log ===\n"
            + error_log
        )
        raw = self.client.complete(
            system=self.system_prompt,
            user=user_msg,
            max_tokens=32000,
        )
        return _split_comp_response(raw)


def _split_comp_response(raw: str) -> tuple[str, list[str]]:
    """Parse Comp's ###NOTES### delimited response."""
    marker = "###NOTES###"
    idx = raw.find(marker)
    if idx == -1:
        return raw.strip(), []
    source = raw[:idx].strip()
    notes_block = raw[idx + len(marker):].strip()
    notes = [line.lstrip("•-* ").strip() for line in notes_block.splitlines() if line.strip()]
    return source, notes


# ---------------------------------------------------------------------------
# LLMContextProvider implementation for Comp's harmonize hook
# ---------------------------------------------------------------------------

class OpenClawLLMProvider:
    """Implements LLMContextProvider using OpenClaw's codex model.

    This wires the existing Comp._harmonize() hook into the OpenClaw API so
    that harmonization is LLM-powered without requiring architectural changes
    to Comp itself.  Full LLM-powered conversion (April/Friday) is handled
    separately in OpenClawConversionDriver.
    """

    def __init__(self, config: OpenClawConfig) -> None:
        self.client = OpenClawClient(config)
        self._harmonize_session = OpenClawAgentSession(
            self.client, "Comp-Harmonize", COMP_HARMONIZATION_SYSTEM
        )
        self._repair_session = OpenClawAgentSession(
            self.client, "Comp-Repair", COMP_REPAIR_SYSTEM
        )

    def harmonize(self, latex_source: str, task_brief: str) -> tuple[str, list[str]]:
        return self._harmonize_session.harmonize(latex_source)

    def repair(self, latex_source: str, error_log: str) -> tuple[str, list[str]]:
        """Ask the repair agent to fix a compile-failed LaTeX source."""
        return self._repair_session.repair_latex(latex_source, error_log)


# ---------------------------------------------------------------------------
# Full LLM-powered conversion driver (used by Comp as orchestrator)
# ---------------------------------------------------------------------------

class OpenClawConversionDriver:
    """Drives the full IEEE↔ACM conversion using OpenClaw LLM agent sessions.

    Comp uses this when OpenClaw is configured to get LLM-quality conversion
    instead of the fallback regex pipeline.  April and Friday are each spawned
    as dedicated agent sessions.
    """

    def __init__(self, config: OpenClawConfig) -> None:
        self.client = OpenClawClient(config)
        self._april = OpenClawAgentSession(self.client, "April", APRIL_SYSTEM)
        self._friday = OpenClawAgentSession(self.client, "Friday", FRIDAY_SYSTEM)

    def convert(
        self,
        latex_source: str,
        direction: str,
    ) -> tuple[str, ConversionReport]:
        """Convert a LaTeX source string using the appropriate LLM agent.

        Pipeline:
          1. Hard-coded pre-processing (deterministic 1:1 substitutions)
          2. Float stripping (figures replaced with %%FLOAT_NNN%% markers)
          3. LLM agent pass (April or Friday handles the structural remainder)
          4. Float restoration
          5. Leakage detection

        Returns the converted LaTeX source and a conversion report.
        """
        if direction == "ieee_to_acm":
            agent = self._april
            source_fmt, target_fmt = "ieee", "acm"
            preprocessed, pre_changes = _preprocess_ieee_to_acm(latex_source)
        elif direction == "acm_to_ieee":
            agent = self._friday
            source_fmt, target_fmt = "acm", "ieee"
            preprocessed, pre_changes = _preprocess_acm_to_ieee(latex_source)
        else:
            raise ValueError(f"Unsupported direction: {direction}")

        # Strip figure/equation environments before LLM conversion and re-inject
        # after — the LLM reliably drops \includegraphics references it can't verify.
        stripped, injections = _strip_floats_for_llm(preprocessed)
        converted_stripped = agent.convert(stripped)
        converted = _restore_floats_after_llm(converted_stripped, injections)

        unresolved = _detect_unresolved(converted, target_fmt)
        report = ConversionReport(
            mapped_fields=["title", "authors", "abstract", "keywords",
                           "sections", "figures", "tables", "equations",
                           "references", "acknowledgments"],
            changed_sections=pre_changes,
            unresolved_items=unresolved,
            warnings=[f"Pre-processor: {c}" for c in pre_changes],
            assumptions=[f"Full LLM conversion via OpenClaw codex ({agent.name})"],
            target_template_profile="acmart-sigconf" if target_fmt == "acm" else "IEEEtran-conference",
            source_format=source_fmt,
            target_format=target_fmt,
        )
        return converted, report


# ---------------------------------------------------------------------------
# Hard-coded pre-processing passes  (run BEFORE the LLM call)
# ---------------------------------------------------------------------------

def _preprocess_ieee_to_acm(latex: str) -> tuple[str, list[str]]:
    """Apply deterministic 1:1 IEEE→ACM macro substitutions before the LLM pass.

    Handles everything that is mechanical and unambiguous so the LLM can focus
    on structurally complex tasks (author blocks, edge cases).

    Returns (processed_latex, list_of_change_descriptions).
    """
    changes: list[str] = []
    result = latex

    # 1. Document class
    result, n = re.subn(
        r'\\documentclass\s*(?:\[[^\]]*\])?\s*\{IEEEtran\}',
        r'\\documentclass[sigconf]{acmart}',
        result, flags=re.S,
    )
    if n:
        changes.append("Replaced IEEEtran documentclass with acmart sigconf")

    # 2. \IEEEPARstart{F}{irst} → First
    result, n = re.subn(
        r'\\IEEEPARstart\{([^}])\}\{([^}]*)\}',
        lambda m: m.group(1) + m.group(2),
        result,
    )
    if n:
        changes.append(f"Replaced {n} \\IEEEPARstart instance(s)")

    # 3. IEEEproof environment
    result, n = re.subn(r'\\begin\{IEEEproof\}', r'\\begin{proof}', result)
    result = result.replace(r'\end{IEEEproof}', r'\end{proof}')
    if n:
        changes.append("Replaced IEEEproof with proof environment")

    # 4. IEEEkeywords → \keywords{}
    def _kw_to_acm(m: re.Match) -> str:
        return r'\keywords{' + m.group(1).strip() + '}'
    result, n = re.subn(
        r'\\begin\{IEEEkeywords\}(.*?)\\end\{IEEEkeywords\}',
        _kw_to_acm, result, flags=re.S,
    )
    if n:
        changes.append("Converted IEEEkeywords to \\keywords{}")

    # 5. bibliographystyle IEEEtran — remove (acmart uses its own)
    result, n = re.subn(r'\\bibliographystyle\{IEEEtran\}', '', result)
    if n:
        changes.append("Removed \\bibliographystyle{IEEEtran}")

    # 6. Remove IEEE-only preamble commands
    ieee_preamble_cmds = [
        r'\\IEEEoverridecommandlockouts\b',
        r'\\overrideIEEEmargins\b',
        r'\\pagestyle\{[^}]*\}',
        r'\\thispagestyle\{[^}]*\}',
    ]
    for pat in ieee_preamble_cmds:
        result, n = re.subn(pat, '', result)
        if n:
            changes.append(f"Removed IEEE-only preamble command: {pat[:40]}")

    # 7. Add graphicx if absent
    if r'\usepackage{graphicx}' not in result and r'\begin{document}' in result:
        result = result.replace(r'\begin{document}',
                                '\\usepackage{graphicx}\n\\begin{document}', 1)
        changes.append("Added \\usepackage{graphicx}")

    return result, changes


def _preprocess_acm_to_ieee(latex: str) -> tuple[str, list[str]]:
    """Apply deterministic 1:1 ACM→IEEE macro substitutions before the LLM pass."""
    changes: list[str] = []
    result = latex

    # 1. Document class
    result, n = re.subn(
        r'\\documentclass\s*(?:\[[^\]]*\])?\s*\{acmart\}',
        r'\\documentclass[conference]{IEEEtran}',
        result, flags=re.S,
    )
    if n:
        changes.append("Replaced acmart documentclass with IEEEtran conference")

    # 2. \keywords{...} → IEEEkeywords environment
    def _kw_to_ieee(m: re.Match) -> str:
        content = m.group(1).strip()
        return f'\\begin{{IEEEkeywords}}\n{content}\n\\end{{IEEEkeywords}}'
    result, n = re.subn(r'\\keywords\{([^}]*)\}', _kw_to_ieee, result)
    if n:
        changes.append("Converted \\keywords{} to IEEEkeywords environment")

    # 3. \begin{acks}...\end{acks} → \section*{Acknowledgment}
    def _acks_to_ieee(m: re.Match) -> str:
        body = m.group(1).strip()
        return f'\\section*{{Acknowledgment}}\n{body}'
    result, n = re.subn(
        r'\\begin\{acks\}(.*?)\\end\{acks\}',
        _acks_to_ieee, result, flags=re.S,
    )
    if n:
        changes.append("Converted acks environment to IEEE Acknowledgment section")

    # 4. Remove ACM-only metadata macros (complete list)
    acm_only_patterns = [
        r'\\ccsdesc(?:\[\d+\])?\{[^}]*\}',
        r'\\begin\{CCSXML\}.*?\\end\{CCSXML\}',
        r'\\acmConference\{[^}]*\}\{[^}]*\}\{[^}]*\}',
        r'\\acmDOI\{[^}]*\}',
        r'\\acmISBN\{[^}]*\}',
        r'\\copyrightyear\{[^}]*\}',
        r'\\acmYear\{[^}]*\}',
        r'\\acmPrice\{[^}]*\}',
        r'\\acmSubmissionID\{[^}]*\}',
        r'\\acmBadge(?:\[[^\]]*\])?\{[^}]*\}',
        r'\\setcopyright\{[^}]*\}',
        r'\\received(?:\[[^\]]*\])?\{[^}]*\}',
        r'\\revised(?:\[[^\]]*\])?\{[^}]*\}',
        r'\\accepted(?:\[[^\]]*\])?\{[^}]*\}',
        r'\\acmVolume\{[^}]*\}',
        r'\\acmNumber\{[^}]*\}',
        r'\\acmArticle\{[^}]*\}',
        r'\\acmMonth\{[^}]*\}',
        r'\\startPage\{[^}]*\}',
        r'\\orcid\{[^}]*\}',
        r'\\authornotemark\[[^\]]*\]',
    ]
    for pat in acm_only_patterns:
        result, n = re.subn(pat, '', result, flags=re.S)
        if n:
            changes.append(f"Removed ACM macro ({n}×): {pat[:50]}")

    # 5. Remove teaserfigure
    result, n = re.subn(
        r'\\begin\{teaserfigure\}.*?\\end\{teaserfigure\}', '',
        result, flags=re.S,
    )
    if n:
        changes.append("Removed teaserfigure environment (no IEEE equivalent)")

    # 6. Add \bibliographystyle{IEEEtran} before \bibliography{} if absent
    if r'\bibliography{' in result and r'\bibliographystyle' not in result:
        result = result.replace(
            r'\bibliography{',
            '\\bibliographystyle{IEEEtran}\n\\bibliography{', 1,
        )
        changes.append("Added \\bibliographystyle{IEEEtran}")

    # 7. biblatex → bibtex: replace \printbibliography with \bibliography{references}
    if r'\printbibliography' in result:
        # Try to recover resource name from \addbibresource
        bib_m = re.search(r'\\addbibresource\{([^}]+)\}', result)
        bib_file = bib_m.group(1).replace('.bib', '') if bib_m else 'references'
        result = re.sub(r'\\printbibliography(?:\[[^\]]*\])?',
                        f'\\\\bibliography{{{bib_file}}}', result)
        changes.append(f"Replaced \\printbibliography with \\bibliography{{{bib_file}}}")

    # 8. Remove \addbibresource (bibtex uses \bibliography)
    result, n = re.subn(r'\\addbibresource\{[^}]*\}', '', result)
    if n:
        changes.append("Removed \\addbibresource (not used with bibtex)")

    # 9. Remove acmart-managed preamble commands
    acm_preamble = [
        r'\\hypersetup\{[^}]*\}',
        r'\\urlstyle\{[^}]*\}',
    ]
    for pat in acm_preamble:
        result, n = re.subn(pat, '', result, flags=re.S)
        if n:
            changes.append(f"Removed ACM-only preamble command: {pat[:50]}")

    # 10. Add graphicx if absent
    if r'\usepackage{graphicx}' not in result and r'\begin{document}' in result:
        result = result.replace(r'\begin{document}',
                                '\\usepackage{graphicx}\n\\begin{document}', 1)
        changes.append("Added \\usepackage{graphicx}")

    return result, changes


def _float_marker_text(key: str) -> str:
    """Return the dual-form marker block inserted in place of a stripped float.

    Uses both a prominent comment AND a \\typeout{} LaTeX command so that even
    if the LLM drops one form the other survives for restoration.
    \\typeout{} is harmless during compile (writes to terminal only).
    """
    return (
        f"\n% [FIGURE PLACEHOLDER {key} — DO NOT REMOVE THIS LINE]\n"
        f"\\typeout{{FLOAT_MARKER_{key}}}\n"
    )


def _strip_floats_for_llm(latex: str) -> tuple[str, dict[str, str]]:
    """Remove figure and display-equation environments from LaTeX before LLM conversion.

    Replaces each environment with a dual-form marker (comment + \\typeout{}) that
    the LLM's system prompt explicitly instructs it to preserve verbatim.
    Returns (stripped_latex, {key: original_block}).
    """
    injections: dict[str, str] = {}
    result = latex

    patterns = [
        (r"\\begin\{figure\*?\}.*?\\end\{figure\*?\}", re.S),
        # Display math blocks that embed images.
        (r"\\\[.*?\\includegraphics.*?\\\]", re.S),
    ]
    counter = 0
    for pattern, flags in patterns:
        def replacer(m: re.Match, _counter: list = [counter]) -> str:
            _counter[0] += 1
            key = f"{_counter[0]:03d}"
            injections[key] = m.group(0)
            return _float_marker_text(key)
        result = re.sub(pattern, replacer, result, flags=flags)
        counter = len(injections)

    return result, injections


def _restore_floats_after_llm(latex: str, injections: dict[str, str]) -> str:
    """Re-insert stripped float blocks at their marker positions.

    Scans for either the comment form or the \\typeout{} form so restoration
    succeeds even when the LLM dropped one of the two marker lines.
    """
    if not injections:
        return latex
    for key, block in injections.items():
        comment_form = f"% [FIGURE PLACEHOLDER {key} — DO NOT REMOVE THIS LINE]"
        typeout_form = f"\\typeout{{FLOAT_MARKER_{key}}}"

        if comment_form in latex:
            # Replace the full dual-marker block (comment + typeout line if present).
            dual = _float_marker_text(key)
            if dual in latex:
                latex = latex.replace(dual, block, 1)
            else:
                latex = latex.replace(comment_form, block, 1)
        elif typeout_form in latex:
            latex = latex.replace(typeout_form, block, 1)
        else:
            # Both marker forms were dropped — append before \end{document}.
            insert_pos = latex.rfind(r"\end{document}")
            if insert_pos != -1:
                latex = latex[:insert_pos] + "\n" + block + "\n" + latex[insert_pos:]
            else:
                latex += "\n" + block
    return latex


def _detect_unresolved(latex: str, target_format: str) -> list[str]:
    """Quick scan for obvious leakage after LLM conversion."""
    issues: list[str] = []
    if target_format == "acm":
        if "IEEEtran" in latex:
            issues.append("Possible IEEEtran class reference remains in ACM output")
        if r"\begin{IEEEkeywords}" in latex:
            issues.append("IEEEkeywords environment not fully converted")
    elif target_format == "ieee":
        if "acmart" in latex:
            issues.append("Possible acmart class reference remains in IEEE output")
        if r"\begin{acks}" in latex:
            issues.append("ACM acks environment not converted to IEEE acknowledgment section")
    return issues


# ---------------------------------------------------------------------------
# Provider presets
# ---------------------------------------------------------------------------

PROVIDER_PRESETS: dict[str, dict] = {
    "openai": {
        "base_url": "https://api.openai.com",
        "token_url": "https://auth.openai.com/oauth/token",
        "authorize_url": "https://auth.openai.com/oauth/authorize",
        "completion_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o",
        "scopes": "openid profile email model.read model.request",
    },
    "openai_mini": {
        "base_url": "https://api.openai.com",
        "token_url": "https://auth.openai.com/oauth/token",
        "authorize_url": "https://auth.openai.com/oauth/authorize",
        "completion_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
        "scopes": "openid profile email model.read model.request",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "token_url": "",   # Anthropic uses API keys, not OAuth — use client_credentials stub
        "authorize_url": "",
        "completion_url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-6",
        "scopes": "",
    },
}


def build_config_for_provider(
    provider: str,
    client_id: str,
    client_secret: str = "",
    model: str = "",
) -> "OpenClawConfig":
    """Build an OpenClawConfig from a named provider preset + credentials."""
    preset = PROVIDER_PRESETS.get(provider.lower())
    if preset is None:
        raise ValueError(
            f"Unknown provider '{provider}'. Known providers: {list(PROVIDER_PRESETS)}"
        )
    return OpenClawConfig(
        base_url=preset["base_url"],
        client_id=client_id,
        client_secret=client_secret,
        model=model or preset["model"],
        token_url=preset["token_url"],
        authorize_url=preset["authorize_url"],
        completion_url=preset["completion_url"],
        scopes=preset["scopes"],
    )


# ---------------------------------------------------------------------------
# OpenClaw Codex — read stored OAuth tokens from auth-profiles.json
# ---------------------------------------------------------------------------

_AUTH_PROFILES_PATH = Path.home() / ".openclaw" / "agents" / "main" / "agent" / "auth-profiles.json"
_OPENCLAW_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
# OpenClaw uses chatgpt.com/backend-api for codex completions, NOT api.openai.com/v1/responses.
# The /codex/responses path is OpenClaw's internal ChatGPT backend endpoint.
_CODEX_COMPLETION_URL = "https://chatgpt.com/backend-api/codex/responses"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # OpenClaw's registered OpenAI app ID


def _extract_account_id_from_token(token: str) -> str:
    """Extract chatgpt_account_id from the JWT payload (no signature verification needed)."""
    import base64
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return ""
        # Add padding if missing.
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        auth_claim = payload.get("https://api.openai.com/auth", {})
        return auth_claim.get("chatgpt_account_id", "")
    except Exception:
        return ""


def _read_auth_profiles() -> dict:
    try:
        return json.loads(_AUTH_PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _best_codex_profile(profiles: dict) -> dict | None:
    """Return the best available openai-codex profile."""
    data = profiles.get("profiles", {})
    # Prefer the lastGood profile.
    last_good_key = profiles.get("lastGood", {}).get("openai-codex", "")
    if last_good_key and last_good_key in data:
        return data[last_good_key]
    # Fall back to any openai-codex profile.
    for key, profile in data.items():
        if key.startswith("openai-codex:"):
            return profile
    return None


def _openclaw_model() -> str:
    """Read the configured default model from openclaw.json."""
    try:
        cfg = json.loads(_OPENCLAW_CONFIG_PATH.read_text(encoding="utf-8"))
        raw = cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary", "")
        # Strip provider prefix: "openai-codex/gpt-5.4" → "gpt-5.4"
        return raw.split("/", 1)[-1] if "/" in raw else raw or "gpt-4o"
    except Exception:
        return "gpt-4o"


def load_config_from_openclaw_codex() -> "OpenClawConfig | None":
    """Load the OpenAI OAuth config that OpenClaw already set up for the user.

    Reads the access + refresh tokens from:
      ~/.openclaw/agents/main/agent/auth-profiles.json

    and the model name from:
      ~/.openclaw/openclaw.json

    No user action required — OpenClaw handles authentication when the user
    logs in once via the app.
    """
    profiles = _read_auth_profiles()
    profile = _best_codex_profile(profiles)
    if not profile:
        return None

    access_token = profile.get("access", "").strip()
    refresh_token = profile.get("refresh", "").strip()
    expires_ms = profile.get("expires", 0)
    if not access_token:
        return None

    model = _openclaw_model()
    config = OpenClawConfig(
        base_url="https://api.openai.com",
        client_id=_CODEX_CLIENT_ID,
        client_secret="",
        model=model,
        token_url=_CODEX_TOKEN_URL,
        completion_url=_CODEX_COMPLETION_URL,
    )
    account_id = _extract_account_id_from_token(access_token)
    # Attach the live token data so the OAuth client uses it directly.
    config._static_token = access_token          # type: ignore[attr-defined]
    config._refresh_token = refresh_token        # type: ignore[attr-defined]
    config._expires_at = expires_ms / 1000.0     # type: ignore[attr-defined]
    config._account_id = account_id              # type: ignore[attr-defined]
    return config


def load_config_from_openclaw_env() -> "OpenClawConfig | None":
    """Fallback: read Anthropic OAuth token injected by Claude Code / OpenClaw env."""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    if not token:
        return None
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
    model = os.environ.get("DEFAULT_LLM_MODEL", "claude-opus-4-6").strip()
    config = OpenClawConfig(
        base_url=base_url,
        client_id="openclaw-workspace",
        client_secret="",
        model=model,
        completion_url=f"{base_url}/v1/messages",
    )
    config._static_token = token  # type: ignore[attr-defined]
    return config


# ---------------------------------------------------------------------------
# Config persistence helpers (used by api.py)
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw_config.json"


def load_config_from_disk() -> "OpenClawConfig | None":
    """Load LLM provider configuration.

    Priority order:
    1. OpenClaw Codex OAuth tokens stored by the app (auth-profiles.json) — preferred
    2. Explicit env vars (OPENCLAW_BASE_URL / CLIENT_ID / CLIENT_SECRET)
    3. Saved config file (~/.openclaw/openclaw_config.json)
    4. Anthropic OAuth token injected by Claude Code env (fallback)
    """
    # 1. OpenClaw Codex — user's Team plan, already authenticated.
    config = load_config_from_openclaw_codex()
    if config:
        return config
    # 2. Explicit env vars.
    config = OpenClawConfig.from_env()
    if config:
        return config
    # 3. Saved config file.
    if _CONFIG_PATH.exists():
        return OpenClawConfig.from_file(_CONFIG_PATH)
    # 4. Anthropic fallback (Claude Code env injection).
    return load_config_from_openclaw_env()


def save_config_to_disk(config: "OpenClawConfig") -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "base_url": config.base_url,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "model": config.model,
        "token_url": config.token_url,
        "completion_url": config.completion_url,
        "authorize_url": config.authorize_url,
    }
    _CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
