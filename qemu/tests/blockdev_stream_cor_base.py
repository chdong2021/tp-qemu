import json
import time

from virttest import data_dir

from provider.blockdev_stream_base import BlockDevStreamTest
from provider.virt_storage.storage_admin import sp_admin
from provider import backup_utils


class BlockdevStreamCORBase(BlockDevStreamTest):
    """Do block-stream with copy-on-read filter as base """

    def __init__(self, test, params, env):
        super(BlockdevStreamCORBase, self).__init__(test, params, env)
        self.snapshot_chain = params["snapshot_chain"].split()

    def prepare_snapshot_file(self):
        self.trash = []
        params = self.params.copy()
        params.setdefault("target_path", data_dir.get_data_dir())
        for snapshot_tag in self.snapshot_chain:
            image = sp_admin.volume_define_by_params(snapshot_tag, params)
            image.hotplug(self.main_vm)
            self.trash.append(image)

    def image_in_backing_file(self, name, backing, depth):
        def _is_backing_exit(backing, depth):
            if isinstance(backing, dict) and "backing" in backing.keys():
                depth -= 1
                if depth != 0:
                    backing = _is_backing_exit(backing["backing"], depth)
            return backing

        backing = _is_backing_exit(backing, depth)
        image = self.get_image_by_tag(name)
        filename = image.image_filename
        if backing["driver"] == "copy-on-read":
            actual_filename = backing["file"]["file"]["filename"]
        else:
            actual_filename = backing["file"]["filename"]
        if filename != actual_filename:
            self.test.fail("file %s not in backing" % filename)

    def check_backing_chain(self):
        out = self.main_vm.monitor.query("block")
        for item in out:
            if self.base_tag in item["qdev"]:
                backing = item["inserted"].get("backing_file")
                if not backing:
                    self.test.fail("Failed to get backing_file for qdev %s"
                                   % self.base_tag)
                backing_dict = json.loads(backing[5:])
                backing_depth = len(self.backing_chain)
                for image_tag in self.backing_chain:
                    check_func = self.image_in_backing_file
                    check_func(image_tag, backing_dict, backing_depth)
                    backing_depth -= 1
                break
        else:
            self.test.fail("Failed to find %s" % self.base_tag)

    def create_snapshot(self):
        self.backing_chain = [self.base_tag]
        options = ["node", "overlay"]
        cmd = "blockdev-snapshot"
        arguments = self.params.copy_from_keys(options)
        for snapshot_tag in self.snapshot_chain:
            arguments["overlay"] = "drive_%s" % snapshot_tag
            self.main_vm.monitor.cmd(cmd, dict(arguments))
            arguments["node"] = "drive_%s" % snapshot_tag
            if snapshot_tag != self.snapshot_chain[-1]:
                self.backing_chain.append(snapshot_tag)
        self._top_device = "drive_%s" % self.snapshot_chain[-1]
        self.check_backing_chain()

    def blockdev_stream(self):
        backup_utils.blockdev_stream(self.main_vm, self._top_device,
                                     **self._stream_options)
        time.sleep(0.5)
        index = self.backing_chain.index(self.params["base_tag"])
        del self.backing_chain[index+1:]
        self.check_backing_chain()

    def do_test(self):
        self.create_snapshot()
        self.blockdev_stream()

    def post_test(self):
        for image in self.trash:
            sp_admin.remove_volume(image)


def run(test, params, env):
    """
    Basic block stream with cor filter node as base

    test steps:
        1. boot VM with copy-on-read filter
        2. create snapshot chains base(cor filter attached)->sn1->sn2->sn3->sn4
        3. check backing chain after snapshot
        4. do block-stream with cor filter node as base
        5. check backing chain after stream

    :param test: test object
    :param params: test configuration dict
    :param env: env object
    """
    stream_test = BlockdevStreamCORBase(test, params, env)
    stream_test.run_test()
