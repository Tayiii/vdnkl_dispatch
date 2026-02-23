# vdnkl_dispatch

## Запуск
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Демо-пользователи
- admin / admin123
- operator / operator123
- field / field123
