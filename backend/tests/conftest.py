import os

os.environ["ANTHROPIC_API_KEY"] = "test-key-not-real"
os.environ["ANTHROPIC_MODEL"] = "test-model"
os.environ["FRONTEND_ORIGIN"] = "http://localhost:3000"
os.environ["MAX_UPLOAD_MB"] = "1"
# The sample invoices are dated October 2026. A huge limit keeps "invoice too old"
# from appearing in tests when this suite is run in a later year.
os.environ["MAX_INVOICE_AGE_DAYS"] = "36500"
os.environ["ACCESS_KEY"] = "test-access-key-0123456789"
os.environ["DATABASE_URL"] = "sqlite://"