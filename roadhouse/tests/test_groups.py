import os
import unittest
import boto
from moto import mock_ec2
import mock
from roadhouse import groups
import yaml

# needs to include ports, TCP/UDP, and

class BaseConfigTestCase(unittest.TestCase):
    @mock_ec2
    def setUp(self):
        self.ec2 = boto.connect_ec2('somekey', 'somesecret')

    @property
    def config(self):
        sample = os.path.dirname(__file__) + "/sample.yaml"
        return groups.SecurityGroupsConfig.load(sample).configure(self.ec2)

def cc(tmp, ec2):
    # shorthand to create a config and apply
    config = groups.SecurityGroupsConfig(tmp).configure(ec2)
    return config.apply()


class CreationTest(BaseConfigTestCase):
    # ensures new groups are created

    @mock_ec2
    def test_creation_no_existing_groups(self):
        c = self.config
        c.apply()
        self.assertEqual(c.updated_group_count, 0)
        self.assertGreater(c.new_group_count, 0)
        c.apply()

    @mock_ec2
    def test_no_description(self):
        tmp = {"test_no_description":
                   {"options": {} }}
        config = groups.SecurityGroupsConfig(tmp).configure(self.ec2)
        config.apply()
        self.assertGreater(config.new_group_count, 0)

class VPCTest(BaseConfigTestCase):
    @mock_ec2
    def test_vpc(self):
        tmp = {"test_vpc":
                   {"options": {"vpc":"vpc_id123"} }}

        c = cc(tmp, self.ec2)

        self.assertGreater(c.new_group_count, 0)
        vpc = self.ec2.get_all_security_groups()[0]
        self.assertEqual(vpc.vpc_id, "vpc_id123")


class RulesParsingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parse = groups.parser.parseString

    def test_tcp_with_ip(self):
        result = self.parse("tcp port 80 192.168.1.1/32")
        self.assertEqual(result.protocol, "tcp")
        self.assertEqual(result.ip_and_mask, "192.168.1.1/32")
        self.assertEqual(result.ports[0], (80, 80))

    def test_multiple_ports(self):
        result = self.parse("tcp port 80, 100 192.168.1.1/32")

        self.assertEqual(result.ports[0], (80,80))
        self.assertEqual(result.ports[1], (100,100))

    # def test_no_tcp_specified(self):
    #     tmp = self.parse("port 80 192.168.1.1")
    #     self.assertEqual("192.168.1.1", tmp.ip)
    #     self.assertEqual(tmp.ports[0], (80,80))

class IPTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.parse = groups.ip.parseString

    def test_ip_no_mask(self):
        # ensurs we get the mask added as /32
        tmp = self.parse("192.168.1.1")[0]
        self.assertEqual("192.168.1.1/32", tmp)

    def test_ip_with_mask(self):
        tmp = self.parse("192.168.1.1/32")[0]
        self.assertEqual("192.168.1.1/32", tmp)

class MaskTest(unittest.TestCase):
    def test_mask(self):
        result = groups.mask.parseString("/32")
        self.assertEqual(result.mask, 32)

class SimplePortParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parse = groups.normalized_port_range.parseString

    def test_single_port(self):
        tmp = self.parse("80")
        self.assertEqual(tmp[0], (80, 80))

    def test_port_range(self):
        tmp = self.parse("80-100")
        self.assertEqual(tmp[0], (80, 100))


class PortParseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parse = groups.ports.parseString

    def test_port_and_range(self):
        tmp = self.parse("22, 80-100")
        self.assertEqual(tmp.ports[0], (22, 22))
        self.assertEqual(tmp.ports[1], (80, 100))

    def test_double_range(self):
        tmp = self.parse("10-20, 80-100")
        self.assertEqual(tmp.ports[0], (10, 20))
        self.assertEqual(tmp.ports[1], (80, 100))

class RuleParseTest(unittest.TestCase):
    def test_single_rule(self):
        result = groups.Rule.parse("tcp port 80 127.0.0.1/32")

        self.assertEqual(len(result), 1)
        tmp = result[0]
        self.assertTrue(isinstance(tmp, groups.Rule))
        self.assertEqual(tmp.from_port, 80)
        self.assertEqual(tmp.to_port, 80)

    def test_group_name_parse(self):
        result = groups.Rule.parse("tcp port 80 web_server")

    def test_sg_parse(self):
        sg = "sg-edcd9784"
        result = groups.Rule.parse("tcp port 80 {}".format(sg))[0]
        self.assertEqual(result.group, sg)


class RemoveExistingRulesTest(unittest.TestCase):

    def setUp2(self):
        # super(RemoveExistingRulesTest, self).setUp()
        self.ec2 = boto.connect_ec2('somekey', 'somesecret')
        self.sg = self.ec2.create_security_group("test_group", "jon is amazing")
        self.sg2 = self.ec2.create_security_group("test_group2", "jon is terrible")
        self.sg.authorize("tcp", 22, 22, "192.168.1.1/32")
        self.sg.authorize("tcp", 100, 110, src_group=self.sg2)
        self.c = groups.SecurityGroupsConfig(None)
        self.c.configure(self.ec2)
        self.c.reload_remote_groups()

    @mock_ec2
    def test_remove_duplicate(self):
        self.setUp2()
        rule = groups.Rule.parse("tcp port 22 192.168.1.1") # should get filtered
        result = self.c.filter_existing_rules(rule, self.sg)
        assert len(result) == 0

    @mock_ec2
    def test_make_sure_wrong_group_isnt_removed(self):
        self.setUp2()
        self.sg2 = self.ec2.create_security_group("test_group3", "jon is not bad")
        self.c.reload_remote_groups()
        rule = groups.Rule.parse("tcp port 100-110 test_group3")
        result = self.c.filter_existing_rules(rule, self.sg)
        assert len(result) == 1

    @mock_ec2
    def test_leave_different_ip(self):
        # should not filtered
        self.setUp2()
        rule = groups.Rule.parse("tcp port 22 192.168.1.2")
        result = self.c.filter_existing_rules(rule, self.sg)
        assert len(result) == 1

    @mock_ec2
    def test_leave_different_protocol(self):
        # should not get filtered
        self.setUp2()
        rule = groups.Rule.parse("udp port 22 192.168.1.1")
        result = self.c.filter_existing_rules(rule, self.sg)
        assert len(result) == 1






