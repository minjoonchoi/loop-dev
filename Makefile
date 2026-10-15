# TARGET is passed through the environment, never interpolated into shell code.
export LOOP_INSTALL_TARGET := $(value TARGET)

.PHONY: install uninstall test
install:
	python3 scripts/install.py --link

uninstall:
	python3 scripts/install.py --uninstall

test:
	python3 -m unittest discover -s tests -v
