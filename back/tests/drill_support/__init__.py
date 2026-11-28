"""Disposable-only operational drill harness for DocMasker jobs.

The implementation is split between :mod:`helpers` (shared doubles, SQL probes
and integrity checks) and :mod:`scenarios` (the crash/resource drills). This
package keeps ``from drill_support import <name>`` working exactly as before the
split: every module-level name is re-exported here, including private helpers.
"""

from .helpers import (
    DrillReport as DrillReport,
)
from .helpers import (
    NoCache as NoCache,
)
from .helpers import (
    _cache_key as _cache_key,
)
from .helpers import (
    _claim as _claim,
)
from .helpers import (
    _config_digest_from_committed_files as _config_digest_from_committed_files,
)
from .helpers import (
    _peak_rss_bytes as _peak_rss_bytes,
)
from .helpers import (
    _pool_checkout_latency as _pool_checkout_latency,
)
from .helpers import (
    _spool_audit as _spool_audit,
)
from .helpers import (
    _sql_expire_lease as _sql_expire_lease,
)
from .helpers import (
    _sql_expire_staging as _sql_expire_staging,
)
from .helpers import (
    _sql_expire_terminal as _sql_expire_terminal,
)
from .helpers import (
    build_docx as build_docx,
)
from .helpers import (
    drill_settings as drill_settings,
)
from .helpers import (
    enqueue_direct as enqueue_direct,
)
from .helpers import (
    result_integrity as result_integrity,
)
from .helpers import (
    synthetic_text as synthetic_text,
)
from .scenarios import (
    backup_restore_rehearsal as backup_restore_rehearsal,
)
from .scenarios import (
    concurrency_batch as concurrency_batch,
)
from .scenarios import (
    crash_after_db_commit as crash_after_db_commit,
)
from .scenarios import (
    crash_before_result_commit as crash_before_result_commit,
)
from .scenarios import (
    crash_during_processing as crash_during_processing,
)
from .scenarios import (
    crash_during_zip as crash_during_zip,
)
from .scenarios import (
    crash_staging as crash_staging,
)
from .scenarios import (
    cross_job_artifact_isolation as cross_job_artifact_isolation,
)
from .scenarios import (
    expiry_and_cleanup as expiry_and_cleanup,
)
from .scenarios import (
    simulate_disk_full as simulate_disk_full,
)
from .scenarios import (
    simulate_pool_exhaustion as simulate_pool_exhaustion,
)
from .scenarios import (
    simulate_sigterm as simulate_sigterm,
)
