# Atlas Signal

Atlas Signal is an experimental news intelligence and event-tracking project.

## Status

**Early experimental stage.** The project uses Python and SQLite. The only working feature is a manual `collect` command for the first experiment: it reads a fixed list of RSS/Atom feeds, stores the articles it finds, and recognises articles it has already seen. Event tracking, entity linking and market analysis do not exist yet.

## Long-term direction

The goal is a general-purpose system that can, over time:

- collect news from multiple sources;
- identify and track developing events;
- connect events to relevant entities such as companies, industries, governments, and projects;
- evaluate whether events have measurable market impacts.

These are goals, not existing features.

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
