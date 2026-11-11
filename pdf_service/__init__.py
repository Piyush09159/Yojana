"""yojana-pdf: multilingual application-guide PDFs for shortlisted schemes (FastAPI + WeasyPrint).

A separate service (Dockerfile.pdf, render.yaml). The main backend calls it with a service token
through web/pdf_client.py; browsers never call it directly. See docs/PDF_SERVICE.md.
"""
