# Contributing

Contributions to mode are very welcome, feel free to open an issue to propose your ideas.

To make a contribution please create a pull request.

## Developing

First it is recommended to fork this repository into your personal Github account then clone your freshly generated fork locally.

### Setup environment

Here are some guidelines to set up your environment:

```sh
$ cd mode/
$ python -m venv env # Create python virtual environment, a `env/` has been created
$ source env/bin/active # Activate the environment
(venv) $ which pip # Ensure everything is well configured
/some/directory/mode/env/bin/pip
```

### Install project dependencies

```sh
(venv) $ pip install -r requirements.txt
Obtaining file:///some/directory/mode
  Installing build dependencies ... done
  Checking if build backend supports build_editable ... done
  Getting requirements to build editable ... done
  Installing backend dependencies ... done
  Preparing editable metadata (pyproject.toml) ... done
Ignoring pre-commit: markers 'python_version < "3.9"' don't match your environment
...
```

This project apply some quality rules on code and commit, to enforce them at commit time you should install the [pre-commit](https://pre-commit.com/) hook:

```sh
(venv) $ pre-commit install
pre-commit installed at .git/hooks/pre-commit
```

### Format & lint the code

You can run the format script to make your change compliant:

```sh
(venv) $ ./scripts/format.sh
+ ruff format mode tests
79 files left unchanged
+ ruff check mode tests --fix
```

_The script uses [ruff](https://github.com/astral-sh/ruff) & [mypy](https://mypy-lang.org/)._

### Run tests

A script is also available to run them:

```
(venv) $ ./scripts/tests.sh
+ pytest tests --cov=mode
Test session starts (platform: linux, Python 3.12.2, pytest 8.1.1, pytest-sugar 1.0.0)
...
```

_The script uses [pytest](https://docs.pytest.org/en/8.0.x/contents.html)._

### Commit format

Commit should be formatted following [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/).

You can commit manually and respect the convention or you can use the cli to help you formatting correctly:

```sh
(venv) $ cz commit
? Select the type of change you are committing docs: Documentation only changes
? What is the scope of this change? (class or file name): (press [enter] to skip)
 README
? Write a short and imperative summary of the code changes: (lower case and no period)
 correct spelling of README
? Provide additional contextual information about the code changes: (press [enter] to skip)

? Is this a BREAKING CHANGE? Correlates with MAJOR in SemVer No
? Footer. Information about Breaking Changes and reference issues that this commit closes: (press [enter] to skip)


docs(README): correct spelling of README
```

## Documentation

To be able to run the documentation locally you should setup your environment and install dependencies, if not already you can read the two first part of the Developing section.

```sh
(venv) $ mkdocs serve
INFO    -  Building documentation...
INFO    -  Cleaning site directory
INFO    -  Documentation built in 1.78 seconds
INFO    -  [19:38:48] Watching paths for changes: 'docs', 'mkdocs.yml'
INFO    -  [19:38:48] Serving on http://127.0.0.1:8000/
```

Then, you can browse the documentation on http://127.0.0.1:8000.

## Maintainers

### Publish a new release

1. First create a new tag and update changelog

```sh
(venv) $ ./scripts/bump.sh
+ cz bump --changelog
bump: version 0.2.0 → 0.2.1
tag to create: 0.2.1
increment detected: PATCH

[master b35722f] bump: version 0.2.0 → 0.2.1
 2 files changed, 2 insertions(+), 1 deletion(-)

...

Done!
```

!!! note
    If this pass it will automatically push the commit and tags to the remote server, you may want to use `cz bump --changelog --dry-run` to check generated changes.

2. Then after ensuring github pass you could publish the release via the Github interface. (An action will triggered and will publish the package to pypi and documentation to the Github pages).
