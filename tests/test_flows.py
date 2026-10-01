from tfg_sdn.odl.flows import flow_ipv4_dst_to_port, flow_table_miss_controller

def test_ipv4_flow_payload():
    f=flow_ipv4_dst_to_port("to_h1","10.0.0.1/32",3)
    assert f["id"]=="to_h1"
    assert f["match"]["ipv4-destination"]=="10.0.0.1/32"
    out=f["instructions"]["instruction"][0]["apply-actions"]["action"][0]["output-action"]["output-node-connector"]
    assert out=="3"

def test_table_miss_goes_to_controller():
    f=flow_table_miss_controller()
    out=f["instructions"]["instruction"][0]["apply-actions"]["action"][0]["output-action"]["output-node-connector"]
    assert out=="CONTROLLER"
