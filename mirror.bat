@echo off
:: Start residential SOCKS tunnel to Hetzner (for tipwin - VPN-blocked)
start /min "Residential Tunnel" bash "%~dp0scripts\socks-tunnel.sh"

cd /d "%~dp0backend"
python run_mirror.py
