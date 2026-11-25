"""``sift``: the sift web inspector (``sift ui``).

A standard-library HTTP server that serves the built React app from
``static/`` (source in ``web/``) and a small JSON API:

==========================  ===================================================
``GET /api/status``         index, extension and stub status; last index run
``GET /api/search``         ``?q=&k=&mode=``: one query with per-stage timings
``GET /api/stats``          index overview + live query latency (``query_log``)
``GET /api/benchmark``      ``eval/results/latest.json`` when a run exists
``GET /api/document``       ``?path=``: full text of an indexed file (reading sheet)
==========================  ===================================================

Every endpoint falls back to the bundled ``preview.json`` while the real thing
is unavailable (no index yet, or a ranking stub still raises
``NotImplementedError``) and says so in a ``source`` field. The page labels
preview data everywhere; it is never presented as a measurement of your build.
"""
