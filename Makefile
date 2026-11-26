.PHONY: test demo install clean

test:
	python3 -m unittest discover -s tests -v

demo:
	python3 -m daythread demo

install:
	python3 -m pip install -e .

clean:
	rm -rf build dist *.egg-info daythread/__pycache__ tests/__pycache__
