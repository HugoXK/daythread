.PHONY: test ci coverage release app bench load docker demo install clean

test:
	python3 -m unittest discover -s tests -v

ci: test
	python3 -m compileall -q daythread tests

coverage:
	rm -f .coverage .coverage.*
	COVERAGE_PROCESS_START=.coveragerc python3 -m coverage run --parallel-mode --source=daythread -m unittest discover -s tests
	python3 -m coverage combine
	python3 -m coverage report -m

release:
	python3 scripts/release.py --build

app:
	./scripts/build_app.sh

bench:
	python3 -m daythread bench

load:
	python3 scripts/load_test.py --facts 2000 --runs 200 --threads 4

docker:
	docker build -t daythread .

demo:
	python3 -m daythread demo

install:
	python3 -m pip install -e .

clean:
	rm -rf build dist *.egg-info daythread/__pycache__ tests/__pycache__
