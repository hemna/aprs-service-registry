WORKDIR?=.
VENVDIR ?= $(WORKDIR)/.aprs-service-registry-venv

.DEFAULT_GOAL := help

.PHONY: dev docs server test

# --- uv-based virtualenv (replaces Makefile.venv) ---
UV ?= uv
VENV = $(VENVDIR)/bin
REQUIREMENTS_TXT ?= requirements.txt
VENV_LOCAL_PACKAGE ?= $(wildcard pyproject.toml)
MARKER = .initialized-with-uv
touch = touch $(1)

VENVDEPENDS :=
ifneq ($(strip $(REQUIREMENTS_TXT)),)
VENVDEPENDS += $(REQUIREMENTS_TXT)
endif
ifneq ($(strip $(VENV_LOCAL_PACKAGE)),)
VENVDEPENDS += $(VENV_LOCAL_PACKAGE)
endif

.PHONY: venv show-venv clean-venv
venv: $(VENV)/$(MARKER)
show-venv: venv
	@$(VENV)/python -c "import sys; print('Python', sys.version.replace(chr(10),' '))"
	@$(UV) --version
	@echo venv: $(VENVDIR)
clean-venv:
	rm -rf $(VENVDIR)

$(VENV):
	$(UV) venv $(VENVDIR)

$(VENV)/$(MARKER): $(VENVDEPENDS) | $(VENV)
ifneq ($(strip $(REQUIREMENTS_TXT)),)
	$(UV) pip install --python $(VENV)/python $(foreach path,$(REQUIREMENTS_TXT),-r $(path))
endif
ifneq ($(strip $(VENV_LOCAL_PACKAGE)),)
	$(UV) pip install --python $(VENV)/python -e .
endif
	$(call touch,$(VENV)/$(MARKER))

EXE ?=
$(VENV)/%$(EXE): $(VENV)/$(MARKER)
	$(UV) pip install --python $(VENV)/python $*
	$(call touch,$@)
# --- end uv-based virtualenv ---

help:	# Help for the Makefile
	@egrep -h '\s##\s' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

dev: REQUIREMENTS_TXT = requirements.txt dev-requirements.txt
dev: venv  ## Create a python virtual environment for development of aprsd

run: venv  ## Create a virtual environment for running aprsd commands

docs: dev
	cp README.rst docs/readme.rst
	cp Changelog docs/changelog.rst
	tox -edocs

clean: clean-build clean-pyc clean-test clean-dev ## remove all build, test, coverage and Python artifacts

clean-build: ## remove build artifacts
	rm -fr build/
	rm -fr dist/
	rm -fr .eggs/
	find . -name '*.egg-info' -exec rm -fr {} +
	find . -name '*.egg' -exec rm -f {} +

clean-pyc: ## remove Python file artifacts
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '*~' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -fr {} +

clean-test: ## remove test and coverage artifacts
	rm -fr .tox/
	rm -f .coverage
	rm -fr htmlcov/
	rm -fr .pytest_cache

clean-dev:
	rm -rf $(VENVDIR)

test: dev  ## Run all the tox tests
	tox -p all

build: test  ## Make the build artifact prior to doing an upload
	$(UV) pip install --python $(VENV)/python build twine
	$(VENV)/python -m build
	$(VENV)/twine check dist/*

upload: build  ## Upload a new version of the plugin
	$(VENV)/twine upload dist/*

check: dev ## Code format check with tox and pep8
	tox -efmt-check
	tox -epep8

fix: dev ## fixes code formatting with gray
	tox -efmt

update-requirements:  ## Update the requirements.txt and dev-requirements.txt files (requires uv)
	rm -f requirements.txt dev-requirements.txt
	$(UV) pip compile -o requirements.txt requirements.in
	$(UV) pip compile -o dev-requirements.txt dev-requirements.in
