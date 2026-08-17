.DEFAULT_GOAL := help

PYTHON := .venv/bin/python
PIP := .venv/bin/pip

# Ghi đè khi gọi make, vd: make run TENANT=karter_school JOB=SMOKE
TENANT   ?= linfox_vn
JOB      ?= SMOKE
FORMATS  ?= console
API_URL  ?= http://103.104.122.36:8000
# File .env dùng cho run-real; đổi để chạy nhiều bộ secret khác nhau, vd:
#   make karter ENV_FILE=.env.karter
ENV_FILE ?= .env

.PHONY: help install venv list test clean \
        run run-mock run-real \
        smoke-linfox smoke-ews smoke-bvc smoke-karter

help: ## Liệt kê các lệnh sẵn có
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

venv: ## Tạo virtualenv ở .venv/ nếu chưa có
	test -d .venv || python3 -m venv .venv

install: venv ## Cài dependency vào .venv/
	$(PIP) install -r requirements.txt

list: ## Liệt kê tenant / job / rule
	$(PYTHON) main.py --list

test: ## Chạy toàn bộ test (plain module, không dùng pytest runner)
	$(PYTHON) -m tests.test_metrics
	$(PYTHON) -m tests.test_s3
	$(PYTHON) -m tests.test_hand_washing
	$(PYTHON) -m tests.test_draw_zone

# Chạy offline: mock client + fake ground truth, không cần mạng/secret.
run-mock: ## Chạy offline (mock + fake sheet). Vd: make run-mock TENANT=linfox_vn JOB=SMOKE
	$(PYTHON) main.py --tenant $(TENANT) --job $(JOB) \
		--analysis mock --source fake --formats $(FORMATS) -v

# Chạy thật: Google Sheet public + API + S3, secret nạp từ $(ENV_FILE) (mặc định .env,
# cp .env.example .env trước; đổi qua ENV_FILE=.env.<tenant> nếu có nhiều bộ secret).
run-real: ## Chạy thật (gsheet_public + http + S3). Vd: make run-real TENANT=karter_school JOB=SMOKE ENV_FILE=.env.karter
	$(PYTHON) main.py --tenant $(TENANT) --job $(JOB) --env-file $(ENV_FILE) \
		--source gsheet --analysis http --formats console json html --skip-health

run-skip-health: ## Chạy thật (gsheet_public + http + S3). Vd: make run-real TENANT=karter_school JOB=SMOKE ENV_FILE=.env.karter
	$(PYTHON) main.py --tenant $(TENANT) --job $(JOB) --env-file $(ENV_FILE) \
		--source gsheet --analysis http --formats console json html

# -t karter_school -j SMOKE --analysis http --source gsheet --formats console json html --skip-health

run: run-real ## Alias của run-mock (mặc định an toàn, không cần secret)

lfvn:
	$(MAKE) run-real TENANT=linfox_vn JOB=REGRESSION ENV_FILE=.env.lfvn

ews:
	$(MAKE) run-real TENANT=ews_usa JOB=SMOKE

bvc:
	$(MAKE) run-real TENANT=bv_c JOB=REGRESSION ENV_FILE=.env.handwashing

karter:
	$(MAKE) run-real TENANT=karter_school JOB=REGRESSION ENV_FILE=.env.karter

clean: ## Xoá report sinh ra + cache python
	rm -rf reports/test_results_* reports/*_ABORTED.* reports/draw_zone_preview
	find . -type d -name __pycache__ -exec rm -rf {} +