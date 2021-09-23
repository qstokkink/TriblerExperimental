from typing import List

from tribler_common.simpledefs import STATE_CHECKPOINTS_LOADED, STATE_LOAD_CHECKPOINTS, STATE_START_LIBTORRENT
from tribler_core.components.base import Component
from tribler_core.components.implementation.masterkey import MasterKeyComponent
from tribler_core.components.implementation.reporter import ReporterComponent
from tribler_core.components.implementation.restapi import RESTComponent
from tribler_core.components.implementation.socks_configurator import SocksServersComponent
from tribler_core.components.implementation.upgrade import UpgradeComponent
from tribler_core.modules.libtorrent.download_manager import DownloadManager
from tribler_core.restapi.rest_manager import RESTManager


class LibtorrentComponent(Component):
    download_manager: DownloadManager
    endpoints: List[str]


class LibtorrentComponentImp(LibtorrentComponent):
    endpoints = ['createtorrent', 'libtorrent', 'torrentinfo', 'downloads', 'channels', 'collections', 'settings']
    rest_manager: RESTManager

    async def run(self):
        await self.use(ReporterComponent, required=False)
        await self.use(UpgradeComponent, required=False)
        socks_ports = (await self.use(SocksServersComponent)).socks_ports
        masterkey = await self.use(MasterKeyComponent)

        config = self.session.config

        # TODO: move rest_manager check after download manager init. Use notifier instead of direct call to endpoint
        rest_manager = self.rest_manager = (await self.use(RESTComponent)).rest_manager
        state_endpoint = rest_manager.get_endpoint('state')

        state_endpoint.readable_status = STATE_START_LIBTORRENT
        download_manager = DownloadManager(
            config=config.libtorrent,
            state_dir=config.state_dir,
            notifier=self.session.notifier,
            peer_mid=masterkey.keypair.key_to_hash(),
            download_defaults=config.download_defaults,
            bootstrap_infohash=config.bootstrap.infohash,
            socks_listen_ports=socks_ports,
            dummy_mode=config.gui_test_mode)
        download_manager.initialize()

        state_endpoint.readable_status = STATE_LOAD_CHECKPOINTS
        await download_manager.load_checkpoints()
        state_endpoint.readable_status = STATE_CHECKPOINTS_LOADED

        self.download_manager = download_manager

        rest_manager.set_attr_for_endpoints(self.endpoints, 'download_manager', download_manager, skip_missing=True)

        if config.gui_test_mode:
            uri = "magnet:?xt=urn:btih:0000000000000000000000000000000000000000"
            await download_manager.start_download_from_uri(uri)

    async def shutdown(self):
        # Release endpoints
        self.rest_manager.set_attr_for_endpoints(self.endpoints, 'download_manager', None, skip_missing=True)

        await self.release(RESTComponent)

        self.download_manager.stop_download_states_callback()
        await self.download_manager.shutdown()
