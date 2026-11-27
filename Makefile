.PHONY: test ci lint coverage release attest icons app tauri tauri-bundle sidecar bench load docker demo install clean

test:
	python3 -m unittest discover -s tests -v

ci: lint test
	python3 -m compileall -q daythread tests

lint:
	python3 -m ruff check daythread tests scripts

coverage:
	rm -f .coverage .coverage.*
	COVERAGE_PROCESS_START=.coveragerc python3 -m coverage run --parallel-mode --source=daythread -m unittest discover -s tests
	python3 -m coverage combine
	python3 -m coverage report -m

release:
	python3 scripts/release.py --build

attest:
	python3 scripts/attest.py --dist dist --signing-key release.pem

icons:
	./scripts/build_icons.sh

sidecar:
	python3 scripts/build_sidecar.py

app:
	./scripts/build_app.sh

tauri:
	cd desktop-tauri/src-tauri && PATH="$$HOME/.cargo/bin:$$PATH" cargo build --release

tauri-bundle:
	cd desktop-tauri && PATH="$$HOME/.cargo/bin:$$PATH" npx --yes @tauri-apps/cli@2 build

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
