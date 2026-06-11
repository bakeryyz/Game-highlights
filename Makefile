.PHONY: run

run:
	@lsof -ti :5001 | xargs kill -9 2>/dev/null || true
	@echo "  http://localhost:5001"
	python app.py
