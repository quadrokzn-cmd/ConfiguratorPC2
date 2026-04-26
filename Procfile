web: python -m scripts.apply_migrations && python -m scripts.bootstrap_admin && uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'
