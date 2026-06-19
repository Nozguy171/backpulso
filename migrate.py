from app import create_app
from app.startup_migrations import run_startup_migrations


app = create_app()
run_startup_migrations(app)
