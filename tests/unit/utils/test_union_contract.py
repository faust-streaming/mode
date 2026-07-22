"""Union-introspection compatibility contract.

The helpers in :mod:`mode.utils.objects` are consumed by downstream projects
such as Faust. These tests make changes in union recognition explicit so a
Mode upgrade cannot silently change which annotations downstream compilers
receive.
"""

import sys
import typing
from typing import Optional, Union, get_args, get