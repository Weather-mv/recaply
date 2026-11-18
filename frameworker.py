"""
frameworker.py — the main script of the editor's frame-drawing worker
processes (see editor._pool). Deliberately empty: on Windows a worker
re-imports its parent's main script, and the app's main script would load
every recap, the OCR models and start background threads. With this stub a
worker only imports editor.py when it gets its first job.
"""
