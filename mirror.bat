@echo off
:: Start SOCKS tunnel to Hetzner (Swedish IP for VBet/ComeOn/Tipwin)
start /min "SOCKS Tunnel" bash "%~dp0scripts\socks-tunnel.sh"

cd /d "%~dp0backend"
python run_mirror.py
