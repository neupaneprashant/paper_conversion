IEEE_MAIN_TEMPLATE = r'''\documentclass[conference]{{IEEEtran}}
\usepackage{{graphicx}}
\usepackage{{amsmath}}
\begin{{document}}
\title{{{title}}}
\author{{{authors}}}
\maketitle
\begin{{abstract}}
{abstract}
\end{{abstract}}
{keywords_block}
{extra_frontmatter}
{body}
\bibliographystyle{{IEEEtran}}
\bibliography{{references}}
\end{{document}}
'''

ACM_MAIN_TEMPLATE = r'''\documentclass[sigconf]{{acmart}}
\usepackage{{graphicx}}
\usepackage{{amsmath}}
\begin{{document}}
\title{{{title}}}
{authors}
\begin{{abstract}}
{abstract}
\end{{abstract}}
{keywords_block}
{extra_frontmatter}
\maketitle
{body}
\bibliographystyle{{ACM-Reference-Format}}
\bibliography{{references}}
\end{{document}}
'''
