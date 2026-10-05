# Atlas Signal

Atlas Signal is an experimental news intelligence and event-tracking project.

## Status

**Early experimental stage.** The project uses Python and SQLite. The working features are two manual commands for the first experiment: `collect` reads a fixed list of RSS/Atom feeds, stores the articles it finds and recognises articles it has already seen; `report` summarizes what has been collected. Event tracking, entity linking and market analysis do not exist yet.

Experiment 001 (feed collection and identity) is described in [docs/experiments/001-feed-collection.md](docs/experiments/001-feed-collection.md).

## Long-term direction

The goal is a general-purpose system that can, over time:

- collect news from multiple sources;
- identify and track developing events;
- connect events to relevant entities such as companies, industries, governments, and projects;
- evaluate whether events have measurable market impacts.

These are goals, not existing features. Ideas and research findings for possible future work are parked in [docs/future-expansions.md](docs/future-expansions.md).

## First experiment

Canada–EU economic cooperation is the likely first real-world tracking experiment. It is a test case only: Atlas Signal is meant to stay general-purpose, not specific to any one region or topic.

## Development

Requires Python 3.12 or newer; development uses Python 3.14. The commands below are for Windows and call the virtual environment's Python directly, so no activation step is needed.

Create the virtual environment and install dependencies:

```
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the tests:

```
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Show the command-line help:

```
.venv\Scripts\python.exe -m atlas_signal --help
```

## Collecting feeds

```
.venv\Scripts\python.exe -m atlas_signal collect
```

Fetches each enabled feed in `config/sources.toml` once, one at a time, and stores articles and sightings in a local SQLite database. It respects robots.txt (including for redirects to another site), sends conditional requests where the feed supports them, and prints a one-line summary per source. Runs are manual; nothing is scheduled.

Options:

- `--db PATH`: database file (default `data/atlas_signal.sqlite3`; the `data/` folder is created if needed and is not tracked by Git).
- `--sources PATH`: source config file (default `config/sources.toml`).
- `--source ID`: collect only this source; repeat to select several.

Exit codes: `0` all sources succeeded, `1` the run finished but at least one source failed or was blocked, `2` the run could not start or complete.

## Reporting

```
.venv\Scripts\python.exe -m atlas_signal report
```

Prints a plain-text summary of collection activity: runs and fetch outcomes, entries and entry errors, sightings and new articles, how articles were matched (GUID or normalized URL), articles seen from more than one source, changed entry fingerprints and feed bodies, and per-source field completeness. It opens the database read-only and never creates, initializes or changes it.

Options:

- `--db PATH`: database file (default `data/atlas_signal.sqlite3`).
- `--since YYYY-MM-DD`: only activity from UTC midnight at the start of that date. The article total is always all-time.

Exit codes: `0` report printed, `2` invalid arguments or a missing, unreadable or incompatible database.
