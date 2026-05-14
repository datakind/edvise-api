# Overview

This repo contains:

* [src/webapp/](https://github.com/datakind/edvise-api/tree/develop/src/webapp): The source code for the SST API (which is called by the SST frontend and by any direct API callers)
* [src/worker/](https://github.com/datakind/edvise-api/tree/develop/src/worker): The source code for the SFTP Worker (which calls the SST API)
* [terraform/]
(https://github.com/datakind/edvise-api/tree/develop/terraform): The Terraform configuration for the SST API/Frontend and other GCP resources including Cloud SQL setup, networking setup, secrets setup
* .devcontainer/ and .vscode/: which allow easy setup if you are using VSCode as your IDE.
* [devtools/](https://github.com/datakind/edvise-api/tree/develop/devtools): is a place to put utility scripts
* .github/: contains mostly copied over files when this directory was forked from the student-success-tool repo, so likely much of it is outdated. The only Github action we've added is the [webapp-and-worker-precommit](https://github.com/datakind/edvise-api/blob/develop/.github/workflows/webapp-and-worker-precommit.yml) which is run on every push to develop. This action contains a python linter (we use [black](https://black.readthedocs.io/en/stable/)), and automated runs of the unit tests in the src/webapp/ and src/worker/ directories.
* Additionally, [pyproject.toml](https://github.com/datakind/edvise-api/blob/develop/pyproject.toml) and [uv.lock](https://github.com/datakind/edvise-api/blob/develop/uv.lock) are important for dependency management. At time of writing, the worker is just skeleton code so there's no separate dependency management. In the long-term consider separating out the dependency management for the two programs. 



## Getting started
### Creating Issues

If you spot a problem, [search if an issue already exists](https://github.com/datakind/edvise-api/issues). If a related issue doesn't exist,
you can open a new issue using a relevant [issue form](https://github.com/datakind/edvise-api/issues/new).

As a general rule, we don’t assign issues to anyone. If you find an issue to work on, you are welcome to open a PR with a fix.


### Environment
We use [Poetry](https://github.com/python-poetry/poetry/tree/master) for package management. To get up and running quickly, install the environment with:
```
poetry install --no-interaction
```


## Code Guidelines

- Use [PEP8](https://www.python.org/dev/peps/pep-0008/);
- Write tests for your new features (please see "Tests" topic below);
- Name identifiers (variables, classes, functions, module names) with readable
  names (`x` is always wrong);
- When manipulating strings, we prefer either [f-string
  formatting](https://docs.python.org/3/tutorial/inputoutput.html#formatted-string-literals)
  (f`'{a} = {b}'`) or [new-style
  formatting](https://docs.python.org/library/string.html#format-string-syntax)
  (`'{} = {}'.format(a, b)`), instead of the old-style formatting (`'%s = %s' % (a, b)`);
- You will know if any test breaks when you commit, and the tests will be run
  again in the continuous integration pipeline (see below);


## Tests

You can type `pytest` to run your tests, no matter which type of test it is.


## Continuous Integration

The [`.github/workflows/lint.yml`](.github/workflows/ci.yml) file configures the CI.




NOTE: this repo was forked from the https://github.com/datakind/student-success-tool repo, which means some of the static files (e.g. CONTRIBUTING.md) may be outdated or may include irrelevant information from that repo. Please update those as you see fit. For information about the specific items listed above, defer to the specific readmes in the relevant directory.
