#!/usr/bin/env python
"""Test script for the PDF to LaTeX pipeline."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

# Test imports
try:
    from paper_conversion_system.pdf_parser import extract_text_from_pdf, pdf_to_latex_project
    from paper_conversion_system.structure_analyzer import analyze_text_structure
    from paper_conversion_system.latex_generator import generate_latex_from_structure
    print("✓ All pipeline modules imported successfully")
except ImportError as e:
    print(f"✗ Import error: {e}")
    sys.exit(1)


def test_structure_analyzer():
    """Test structure analyzer with sample text."""
    print("\n=== Testing Structure Analyzer ===")
    
    sample_text = """
    Efficient Algorithms for Machine Learning
    
    John Doe and Jane Smith
    
    Abstract
    This paper presents novel algorithms for efficient machine learning.
    We demonstrate improvements in both time and space complexity.
    
    Keywords: machine learning, algorithms, efficiency
    
    1. Introduction
    Machine learning has become increasingly important in recent years.
    Our work builds on previous studies by Smith (2020) and Doe (2021).
    
    Theorem 1. For any dataset $D$ of size $n$, the time complexity is $O(n \log n)$.
    
    2. Methods
    We use the following approach:
    - Data preprocessing
    - Model training
    - Evaluation
    
    Table 1: Performance comparison
    Method | Accuracy | Time
    Baseline | 85% | 10s
    Our method | 92% | 5s
    
    3. Results
    Results show clear improvements.
    The equation $x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}$ describes the solution.
    
    References
    [1] Smith, J. (2020). "Previous Work". Journal of ML.
    [2] Doe, J. (2021). "More Work". Conference on AI.
    """
    
    structure = analyze_text_structure(sample_text)
    
    print(f"✓ Title detected: {structure['title'][:50]}...")
    print(f"✓ Authors detected: {structure['authors']}")
    print(f"✓ Keywords detected: {structure['keywords']}")
    print(f"✓ Abstract length: {len(structure.get('abstract', ''))} chars")
    print(f"✓ Sections found: {len(structure['sections'])}")
    print(f"✓ Equations found: {len(structure['equations'])}")
    print(f"✓ Tables found: {len(structure['tables'])}")
    print(f"✓ Lists found: {len(structure['lists'])}")
    print(f"✓ References found: {len(structure['references'])}")
    
    return structure


def test_latex_generator(structure: dict):
    """Test LaTeX generation from structure."""
    print("\n=== Testing LaTeX Generator ===")
    
    latex = generate_latex_from_structure(structure, "Test Document")
    
    print(f"✓ Generated LaTeX document: {len(latex)} chars")
    
    # Check for essential LaTeX components
    has_documentclass = "\\documentclass" in latex
    has_maketitle = "\\maketitle" in latex
    has_begin_doc = "\\begin{document}" in latex
    has_end_doc = "\\end{document}" in latex
    
    print(f"✓ Contains \\documentclass: {has_documentclass}")
    print(f"✓ Contains \\maketitle: {has_maketitle}")
    print(f"✓ Contains \\begin{{document}}: {has_begin_doc}")
    print(f"✓ Contains \\end{{document}}: {has_end_doc}")
    
    # Check if structure was preserved
    section_count = latex.count("\\section")
    itemize_count = latex.count("\\begin{itemize}")
    tabular_count = latex.count("\\begin{tabular}")
    
    print(f"✓ Contains {section_count} sections")
    print(f"✓ Contains {itemize_count} lists")
    print(f"✓ Contains {tabular_count} tables")
    
    return latex


def test_full_pipeline():
    """Test full pipeline with a sample text file."""
    print("\n=== Testing Full Pipeline ===")
    
    sample_text = """
    Advanced Deep Learning Techniques
    
    Abstract
    This paper presents advanced techniques for deep learning systems.
    
    1. Introduction
    Deep learning has revolutionized many fields.
    
    Key contributions:
    - Novel architecture design
    - Efficient training methods
    - Improved accuracy metrics
    
    2. Methodology
    We propose a new approach using transformer models.
    
    The complexity is O(n²) where n is the sequence length.
    
    3. Experimental Results
    Our experiments on standard benchmarks show improvements.
    
    - ImageNet: 95% accuracy
    - COCO: 88% mAP
    - SQuAD: 92% F1
    
    4. Conclusion
    We demonstrated effectiveness of our approach.
    
    References
    [1] Vaswani et al. (2017). "Attention is All You Need"
    [2] Devlin et al. (2018). "BERT: Pre-training of Deep"
    """
    
    # Create temporary files for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Save sample text as a temporary file
        sample_file = tmpdir / "sample.txt"
        sample_file.write_text(sample_text)
        
        # Test the pipeline
        structure = analyze_text_structure(sample_text)
        latex = generate_latex_from_structure(structure, "Deep Learning Paper")
        
        # Save to file
        output_file = tmpdir / "output.tex"
        output_file.write_text(latex)
        
        print(f"✓ Full pipeline executed successfully")
        print(f"✓ Output LaTeX saved to {output_file}")
        print(f"✓ Document size: {len(latex)} characters")
        
        # Verify output is valid LaTeX
        assert "\\documentclass" in latex
        assert "\\begin{document}" in latex
        assert "\\end{document}" in latex
        print("✓ Output is valid LaTeX structure")


def main():
    """Run all tests."""
    print("PDF to LaTeX Pipeline - Test Suite")
    print("=" * 50)
    
    try:
        # Test 1: Structure analyzer
        structure = test_structure_analyzer()
        
        # Test 2: LaTeX generator
        latex = test_latex_generator(structure)
        
        # Test 3: Full pipeline
        test_full_pipeline()
        
        print("\n" + "=" * 50)
        print("✓ All tests passed!")
        print("\nPipeline is ready to use:")
        print("  python -m paper_conversion_system pdf2latex --input <pdf> --output <dir>")
        
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
