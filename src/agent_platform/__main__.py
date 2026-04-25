"""包入口：python -m agent_platform"""
from agent_platform.interfaces.cli import cli_main
import sys

sys.exit(cli_main())
