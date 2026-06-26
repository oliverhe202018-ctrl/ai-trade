@echo off
REM autonomous_week_001_day1_premarket_once
REM One-shot scheduled premarket for Day 1
REM Triggered by Windows Task Scheduler
C:\Users\a2515\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe C:\Users\a2515\ai-trader\scripts\scheduled_day1_premarket.py > C:\Users\a2515\ai-trader\logs\autonomous_week_001\day1_scheduled_premarket_cron.log 2>&1
