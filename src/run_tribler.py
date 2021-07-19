import asyncio
import logging.config
import os
import signal
import sys
from asyncio import Event

from PyQt5.QtCore import QSettings

import tribler_core
import tribler_gui
from tribler_common.network_utils import NetworkUtils
from tribler_common.sentry_reporter.sentry_reporter import SentryReporter, SentryStrategy
from tribler_common.sentry_reporter.sentry_scrubber import SentryScrubber
from tribler_common.version_manager import VersionHistory
from tribler_core.config.tribler_config import TriblerConfig
from tribler_core.dependencies import check_for_missing_dependencies
from tribler_core.modules.bandwidth_accounting.component import BandwidthAccountingComponent
from tribler_core.modules.ipv8.component import Ipv8Component, DiscoveryCommunityComponent, \
    DHTDiscoveryCommunityComponent, MyPeerComponent, Ipv8BootstrapperComponent
from tribler_core.modules.libtorrent.component import LibtorrentComponent
from tribler_core.modules.libtorrent.download_manager import DownloadManager

from tribler_core.modules.metadata_store.community.component import GigaChannelComponent
from tribler_core.modules.metadata_store.component import MetadataStoreComponent
from tribler_core.modules.metadata_store.manager.component import GigachannelManagerComponent
from tribler_core.modules.payout.component import PayoutComponent
from tribler_core.modules.popularity.component import PopularityComponent
from tribler_core.modules.resource_monitor.component import ResourceMonitorComponent
from tribler_core.modules.torrent_checker.component import TorrentCheckerComponent
from tribler_core.modules.tunnel.component import TunnelsComponent
from tribler_core.modules.version_check.component import VersionCheckComponent
from tribler_core.modules.watch_folder.component import WatchFolderComponent
from tribler_core.restapi.component import RESTComponent
from tribler_core.session import core_session
from tribler_core.utilities.osutils import get_root_state_directory
from tribler_core.version import sentry_url, version_id
from tribler_gui.utilities import get_translator

logger = logging.getLogger(__name__)
CONFIG_FILE_NAME = 'triblerd.conf'


# pylint: disable=import-outside-toplevel


def components_gen(config: TriblerConfig):
    components_list = [
        (RESTComponent, config.api.http_enabled or config.api.https_enabled),
        # (UpgradeComponent, config.upgrader_enabled and not config.core_test_mode),
        (MetadataStoreComponent, config.chant.enabled),
        #(DHTDiscoveryCommunityComponent, config.ipv8.enabled),
        (MyPeerComponent, config.ipv8.enabled),
        #(Ipv8BootstrapperComponent, config.ipv8.enabled),
        #(DiscoveryCommunityComponent, config.ipv8.enabled),
        #(Ipv8Component, config.ipv8.enabled),
        (LibtorrentComponent, config.libtorrent.enabled),
        #(TunnelsComponent, config.ipv8.enabled and config.tunnel_community.enabled),
        #(BandwidthAccountingComponent, config.ipv8.enabled),
        #(PayoutComponent, config.ipv8.enabled),
        #(TorrentCheckerComponent, config.torrent_checking.enabled and not config.core_test_mode),
        #(PopularityComponent, config.ipv8.enabled and config.popularity_community.enabled),
        #(GigaChannelComponent, config.chant.enabled),
        #(WatchFolderComponent, config.watch_folder.enabled),
        (ResourceMonitorComponent, config.resource_monitor.enabled and not config.core_test_mode),
        #(VersionCheckComponent, config.general.version_checker_enabled and not config.core_test_mode),
        #(GigachannelManagerComponent,
        # config.chant.enabled and config.chant.manager_enabled and config.libtorrent.enabled)
    ]

    for component, condition in components_list:
        if condition:
            yield component()


def set_anon_proxy_settings(config):
    anon_proxy_ports = config.tunnel_community.socks5_listen_ports
    if not anon_proxy_ports:
        anon_proxy_ports = [NetworkUtils().get_random_free_port() for _ in range(5)]
        config.tunnel_community.socks5_listen_ports = anon_proxy_ports
    anon_proxy_settings = ("127.0.0.1", anon_proxy_ports)
    logger.info(f'Set anon proxy settings: {anon_proxy_settings}')

    DownloadManager.set_anon_proxy_settings(config.libtorrent, 2, anon_proxy_settings)


def start_tribler_core(base_path, api_port, api_key, root_state_dir, core_test_mode=False):
    """
    This method will start a new Tribler session.
    Note that there is no direct communication between the GUI process and the core: all communication is performed
    through the HTTP API.
    """
    logger.info(f'Start tribler core. Base path: "{base_path}". API port: "{api_port}". '
                f'API key: "{api_key}". Root state dir: "{root_state_dir}". '
                f'Core test mode: "{core_test_mode}"')

    from tribler_core.check_os import check_and_enable_code_tracing, set_process_priority
    tribler_core.load_logger_config(root_state_dir)

    from tribler_core.config.tribler_config import TriblerConfig
    from tribler_core.modules.process_checker import ProcessChecker

    trace_logger = None

    # TODO for the moment being, we use the SelectorEventLoop on Windows, since with the ProactorEventLoop, ipv8
    # peer discovery becomes unstable. Also see issue #5485.
    if sys.platform.startswith('win'):
        asyncio.set_event_loop(asyncio.SelectorEventLoop())

    sys.path.insert(0, base_path)

    async def start_tribler():
        # Check if we are already running a Tribler instance
        process_checker = ProcessChecker(root_state_dir)
        if process_checker.already_running:
            return
        process_checker.create_lock_file()

        from asyncio import get_event_loop
        from tribler_core.exception_handler import CoreExceptionHandler
        get_event_loop().set_exception_handler(CoreExceptionHandler.unhandled_error_observer)

        # Before any upgrade, prepare a separate state directory for the update version so it does not
        # affect the older version state directory. This allows for safe rollback.
        version_history = VersionHistory(root_state_dir)
        version_history.fork_state_directory_if_necessary()
        version_history.save_if_necessary()
        state_dir = version_history.code_version.directory
        config = TriblerConfig.load(file=state_dir / CONFIG_FILE_NAME, state_dir=state_dir, reset_config_on_error=True)
        config.core_test_mode = core_test_mode

        if not config.error_handling.core_error_reporting_requires_user_consent:
            SentryReporter.global_strategy = SentryStrategy.SEND_ALLOWED

        config.api.http_port = int(api_port)
        # If the API key is set to an empty string, it will remain disabled
        if config.api.key not in ('', api_key):
            config.api.key = api_key
            config.write()  # Immediately write the API key so other applications can use it
        config.api.http_enabled = True

        priority_order = config.resource_monitor.cpu_priority
        set_process_priority(pid=os.getpid(), priority_order=priority_order)

        global trace_logger
        # Enable tracer if --trace-debug or --trace-exceptions flag is present in sys.argv
        log_dir = config.general.get_path_as_absolute('log_dir', config.state_dir)
        trace_logger = check_and_enable_code_tracing('core', log_dir)

        shutdown_event = Event()
        signal.signal(signal.SIGTERM, lambda signum, stack: shutdown_event.set)
        # Run until core_session exits
        set_anon_proxy_settings(config)
        await core_session(config, components=list(components_gen(config)), shutdown_event=shutdown_event)

        if trace_logger:
            trace_logger.close()

        process_checker.remove_lock_file()
        # Flush the logs to the file before exiting
        for handler in logging.getLogger().handlers:
            handler.flush()

    logging.getLogger('asyncio').setLevel(logging.WARNING)
    asyncio.run(start_tribler())


def init_sentry_reporter():
    """ Initialise sentry reporter

    We use `sentry_url` as a URL for normal tribler mode and TEST_SENTRY_URL
    as a URL for sending sentry's reports while a Tribler client running in
    test mode
    """
    test_sentry_url = os.environ.get('TEST_SENTRY_URL', None)

    if not test_sentry_url:
        SentryReporter.init(sentry_url=sentry_url,
                            release_version=version_id,
                            scrubber=SentryScrubber(),
                            strategy=SentryStrategy.SEND_ALLOWED_WITH_CONFIRMATION)
        logger.info('Sentry has been initialised in normal mode')
    else:
        SentryReporter.init(sentry_url=test_sentry_url,
                            release_version=version_id,
                            scrubber=None,
                            strategy=SentryStrategy.SEND_ALLOWED)
        logger.info('Sentry has been initialised in debug mode')


def init_boot_logger():
    # this logger config will be used before Core and GUI
    #  set theirs configs explicitly
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)


if __name__ == "__main__":
    init_boot_logger()
    init_sentry_reporter()

    # Get root state directory (e.g. from environment variable or from system default)
    root_state_dir = get_root_state_directory()
    logger.info(f'Root state dir: {root_state_dir}')

    # Check whether we need to start the core or the user interface
    if 'CORE_PROCESS' in os.environ:
        logger.info('Running in "core" mode')

        # Check for missing Core dependencies
        check_for_missing_dependencies(scope='core')
        base_path = os.environ['CORE_BASE_PATH']
        api_port = os.environ['CORE_API_PORT']
        api_key = os.environ.get('CORE_API_KEY')
        core_test_mode = bool(os.environ.get("TRIBLER_CORE_TEST_MODE", False))

        start_tribler_core(base_path, api_port, api_key, root_state_dir, core_test_mode=core_test_mode)
    else:
        logger.info('Running in "normal" mode')

        # Workaround for macOS Big Sur, see https://github.com/Tribler/tribler/issues/5728
        if sys.platform == "darwin":
            logger.info('Enabling a workaround for macOS Big Sur')
            os.environ["QT_MAC_WANTS_LAYER"] = "1"

        # Set up logging
        tribler_gui.load_logger_config(root_state_dir)

        # Check for missing both(GUI, Core) dependencies
        check_for_missing_dependencies(scope='both')

        # Do imports only after dependencies check
        from tribler_core.check_os import check_and_enable_code_tracing, check_environment, check_free_space, \
            enable_fault_handler, error_and_exit, should_kill_other_tribler_instances
        from tribler_core.exceptions import TriblerException

        try:
            # Enable tracer using commandline args: --trace-debug or --trace-exceptions
            trace_logger = check_and_enable_code_tracing('gui', root_state_dir)

            enable_fault_handler(root_state_dir)

            # Exit if we cant read/write files, etc.
            check_environment()

            should_kill_other_tribler_instances(root_state_dir)

            check_free_space()

            from tribler_gui.tribler_app import TriblerApplication
            from tribler_gui.tribler_window import TriblerWindow

            app_name = os.environ.get('TRIBLER_APP_NAME', 'triblerapp')
            app = TriblerApplication(app_name, sys.argv)
            # ACHTUNG! translator MUST BE created and assigned to a separate variable
            # BEFORE calling installTranslator on app. Otherwise, it won't work for some reason

            settings = QSettings('nl.tudelft.tribler')
            translator = get_translator(settings.value('translation', None))
            app.installTranslator(translator)

            if app.is_running():
                logger.info('Application is running')
                for arg in sys.argv[1:]:
                    if os.path.exists(arg) and arg.endswith(".torrent"):
                        app.send_message(f"file:{arg}")
                    elif arg.startswith('magnet'):
                        app.send_message(arg)

                sys.exit(1)

            logger.info('Start Tribler Window')
            window = TriblerWindow(settings)
            window.setWindowTitle("Tribler")
            app.set_activation_window(window)
            app.parse_sys_args(sys.argv)
            sys.exit(app.exec_())

        except ImportError as ie:
            logger.exception(ie)
            error_and_exit("Import Error", f"Import error: {ie}")

        except TriblerException as te:
            logger.exception(te)
            error_and_exit("Tribler Exception", f"{te}")

        except SystemExit:
            logger.info("Shutting down Tribler")
            if trace_logger:
                trace_logger.close()
            # Flush all the logs to make sure it is written to file before it exits
            for handler in logging.getLogger().handlers:
                handler.flush()
            raise
