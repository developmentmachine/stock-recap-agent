"""包入口：python -m stock_recap"""
from stock_recap.interfaces.cli import cli_main
import sys

sys.exit(cli_main())
