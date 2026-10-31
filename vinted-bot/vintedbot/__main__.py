import sys

if sys.version_info < (3, 11):
    sys.exit(f"VintedBot nécessite Python 3.11 ou plus récent (vous avez {sys.version.split()[0]}).\n"
             "Téléchargez la dernière version sur https://www.python.org/downloads/")

from .cli import main  # noqa: E402

sys.exit(main())
