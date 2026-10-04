# Atlas Signal

Atlas Signal is an experimental news intelligence and event-tracking project.

## Status

**Early experimental stage. No application functionality has been built yet.** The project uses Python and SQLite. Work has started on the first experiment, feed-based news collection; so far only an empty package scaffold exists.

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
