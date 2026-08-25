from app.parsers.python_parser import parse_python


def test_python_init_injects_and_publish():
    code = '''
class Worker:
    def __init__(self, bus: EventBus):
        self.bus = bus

    def run(self):
        self.bus.publish("ready")
        self.bus.subscribe("done")
'''
    result = parse_python(code)
    assert any(d.type == "INJECTS" and d.target_name == "EventBus" for d in result.dependencies)
    assert any(d.type == "PUBLISHES" for d in result.dependencies)
    assert any(d.type == "CONSUMES" for d in result.dependencies)
