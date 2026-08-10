# KnightFightBot

A multi-threaded automation and analytics engine for a browser-based MMO, built to manage ~30 game accounts across multiple servers unattended.

## What it does

- Runs 5 concurrent daemon threads per bot instance: action loop (shopping, training, missions), ranking scraper, slow-cycle stats/profile cache, background bootstrap, and an HTTP dashboard server
- Combat outcome model calibrated by curve-fitting 3,500+ real battle results, used to simulate fights before committing to them in-game
- Session resilience: detects character resets, expired cookies and CSRF failures, then re-authenticates automatically and backs up local state before recovering
- Web dashboard (launcher.py + launcher.html) to manage multiple profiles, view live status and start/stop bots from the browser
- Packed as a standalone Windows executable (PyInstaller spec + batch installer/updater scripts) so non-technical users can run it

## Stack

Python, threading, requests (session/cookie management), a small stdlib HTTP server for the dashboard, PyInstaller for packaging.

This is a personal automation project built for a browser game, shared here as a sample of concurrent programming, resilient scraping, and turning real usage data into a predictive model.
