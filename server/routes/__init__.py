"""
The HTTP surface, one module per group of routes. `server/main.py` builds the
app and includes these; nothing here constructs a FastAPI() of its own.

Auth is the exception that predates this package — it still lives in
`server/auth.py`, because `current_user()` (the dependency every route below
takes) belongs next to the cookie handling that produces it.
"""
