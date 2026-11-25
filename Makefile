# Tentora — certification practice exams, one folder per exam under exams/.
#
# Common targets (EXAM=<slug> picks the exam; default data-engineer-associate):
#   make exams       List the available exam profiles.
#   make plan        Blueprint + coverage: questions per section and per guide objective.
#   make take        Sit an exam in the terminal, drawn from all banks of EXAM.
#   make retry       Re-take only the questions you got wrong in your last attempt.
#   make generate    Generate COUNT more questions with Claude, grounded in the official docs
#                    and independently verified (FOCUS=weak targets your weak sections).
#   make reverify    Re-check the sourced questions of EXAM against today's docs; wrong ones are reported.
#   make profile     Create a new exam from its official guide: make profile GUIDE=x.pdf EXAM=slug
#   make grade       Grade a saved answers file (BANK=... ANSWERS=...), with review.
#   make validate    Check every bank of EXAM: structure, objectives, sources, outdated names.
#   make web         Start the browser exam in Docker  -> http://localhost:8080
#   make web-stop    Stop the Docker container.
#   make web-logs    Follow the container's logs.
#   make web-local   Run the browser exam without Docker (python3 only).
#   make test        Run the unit tests.

PYTHON      ?= python3
EXAM        ?= data-engineer-associate
BANKS       ?=
BANK        ?= exams/$(EXAM)/banks/generated-1.json
COUNT       ?=
OUT         ?=
MODEL       ?= opus
VERIFY_MODEL ?= sonnet
FOCUS       ?= blueprint
VERIFY      ?= 1
UNSCORED    ?= 0
GUIDE       ?=
ANSWERS     ?=
SEED        ?=
WEB_PORT    ?= 8080
COMPOSE     ?= docker compose

export EXAM

.PHONY: exams plan take retry generate reverify profile grade validate help web web-stop web-logs web-local test

help:
	@grep -E '^#   make' Makefile | sed 's/^# //'

exams:
	@for d in exams/*/profile.json; do printf '  %-32s ' "$$(basename $$(dirname $$d))"; \
		$(PYTHON) -c "import json,sys; p=json.load(open(sys.argv[1])); print(p['title'], '· guide', p.get('guideVersion','?'))" $$d; done

plan:
	@$(PYTHON) scripts/bank_tools.py plan

# BANKS=... (bank files) narrows the pool; default is every bank of EXAM.
# UNSCORED=<n> mixes in n unscored items, like the real exam. SEED=<n> is reproducible.
take:
	@$(PYTHON) scripts/score.py take $(BANKS) --unscored $(UNSCORED) $(if $(SEED),--seed $(SEED))

# `take` writes results/<exam>/<timestamp>.md plus results/<exam>/wrong-latest.json.
# Each retry overwrites it with what you still got wrong.
retry:
	@test -f results/$(EXAM)/wrong-latest.json || { echo "  No wrong answers saved — run 'make take' first."; exit 1; }
	@$(PYTHON) scripts/score.py take results/$(EXAM)/wrong-latest.json $(if $(SEED),--seed $(SEED))

# Writes exams/<exam>/banks/generated-<n>.json unless OUT=path is given.
# Shells out to the `claude` CLI, reusing its login — no API key needed.
generate:
	@$(PYTHON) scripts/generate_questions.py --model $(MODEL) --verify-model $(VERIFY_MODEL) --focus $(FOCUS) \
		$(if $(COUNT),--count $(COUNT)) $(if $(OUT),--out $(OUT)) $(if $(filter 0,$(VERIFY)),--no-verify)

reverify:
	@$(PYTHON) scripts/generate_questions.py --reverify --verify-model $(VERIFY_MODEL)

profile:
	@test -n "$(GUIDE)" || { echo "  Give the exam guide: make profile GUIDE=guide.pdf EXAM=<slug>"; exit 1; }
	@$(PYTHON) scripts/make_profile.py --guide "$(GUIDE)" --exam $(EXAM) --model $(MODEL)

grade:
	@test -n "$(ANSWERS)" || { echo "  Give an answers file: make grade ANSWERS=my-answers.json"; exit 1; }
	@$(PYTHON) scripts/score.py grade $(BANK) $(ANSWERS) --review

validate:
	@$(PYTHON) scripts/bank_tools.py validate

# Browser exam in Docker. results/ is shared with `make take`/`make retry`.
web:
	@mkdir -p results
	@EXAM_UID=$$(id -u) EXAM_GID=$$(id -g) EXAM_WEB_PORT=$(WEB_PORT) $(COMPOSE) up -d --build
	@echo "  Tentora running at http://localhost:$(WEB_PORT)"

web-stop:
	@$(COMPOSE) down

web-logs:
	@$(COMPOSE) logs -f

web-local:
	@EXAM_PORT=$(WEB_PORT) $(PYTHON) webapp/server.py

test:
	@$(PYTHON) -m unittest discover -s tests -v
