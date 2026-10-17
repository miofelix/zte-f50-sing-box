PYTHON ?= python3
PYTHONPATH := src

.PHONY: check test test-unit test-safety help

check:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m compileall -q src tests deploy.py test_safety.py
	sh -n f50-proxy
	sh -n boot-start.sh
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -c "from zte_f50_singbox.profiles import load_profile; print(load_profile('zte-f50')['display_name'])"
	$(PYTHON) deploy.py --help >/dev/null

test: test-unit test-safety

test-unit:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests -v

test-safety:
	$(PYTHON) test_safety.py

help:
	@echo 'make check  - fast syntax/profile/CLI checks'
	@echo 'make test-unit - fast offline deployment/boot tests'
	@echo 'make test   - unit tests and local transaction fault injection'
