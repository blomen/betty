-- Create the market database for tick/candle data
SELECT 'CREATE DATABASE market OWNER betty'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'market')\gexec
