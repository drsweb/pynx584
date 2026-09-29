import flask
import json
import logging


LOG = logging.getLogger('api')
_controller = None
app = flask.Flask('nx584')


def set_controller(ctrl):
    """Set the controller instance for API handlers."""
    global _controller
    _controller = ctrl

werkzeug_logger = logging.getLogger('werkzeug')
app.logger = logging.getLogger('nx584.main')


def _check_protocol_support(endpoint_name):
    if not _controller:
        return True
    return endpoint_name not in _controller.protocol_unsupported_endpoints


def show_zone(zone):
    return {
        'number': zone.number,
        'name': zone.name,
        'state': zone.state,
        'bypassed': zone.bypassed,
        'condition_flags': zone.condition_flags,
        'type_flags': zone.type_flags,
    }


def show_partition(partition):
    return {
        'number': partition.number,
        'condition_flags': partition.condition_flags,
        'armed': 'Armed' in partition.condition_flags,
        'last_user': partition.last_user,
    }


def show_user(user):
    if all([x > 9 for x in user.pin]):
        pin = None
    else:
        pin = ''.join([str(c) if c < 10 else '' for c in user.pin])
    return {
        'number': user.number,
        'pin': pin,
        'authority_flags': user.authority_flags,
        'authorized_partitions': user.authorized_partitions,
    }


@app.route('/zones')
def index_zones():
    try:
        result = json.dumps({
            'zones': [show_zone(zone) for zone in _controller.zones.values()]})
        return flask.Response(result,
                              mimetype='application/json')
    except Exception as e:
        LOG.exception('Failed to index zones')


@app.route('/partitions')
def index_partitions():
    try:
        result = json.dumps({
            'partitions': [show_partition(partition)
                           for partition in _controller.partitions.values()]})
        return flask.Response(result,
                              mimetype='application/json')
    except Exception as e:
        LOG.exception('Failed to index partitions')


@app.route('/command')
def command():
    args = flask.request.args
    if args.get('cmd') == 'set_time':
        if _controller and not _controller.protocol_features.get('set_time', True):
            return flask.jsonify({'error': 'Protocol does not support set_time'}), 501
    if args.get('cmd') == 'arm':
        if args.get('type') == 'stay':
            _controller.arm_stay(int(args.get('partition', 1)))
        elif args.get('type') == 'exit':
            _controller.arm_exit(int(args.get('partition', 1)))
        else:
            _controller.arm_auto(int(args.get('partition', 1)))
    elif args.get('cmd') == 'disarm':
        _controller.disarm(args.get('master_pin'), int(args.get('partition', 1)))
    elif args.get('cmd') == 'police':
        _controller.police_panic(int(args.get('partition', 1)))
    elif args.get('cmd') == 'fire':
        _controller.fire_panic(int(args.get('partition', 1)))
    elif args.get('cmd') == 'status':
        if _controller and hasattr(_controller, 'messaging'):
            _controller.publish_all()
        return flask.Response(json.dumps({
            'status': 'ok',
            'message': 'Full state republish triggered'
        }), mimetype='application/json')
    return flask.Response()


@app.route('/zones/<int:zone>', methods=['PUT'])
def put_zone(zone):
    zone = _controller.zones.get(zone)
    if not zone:
        flask.abort(404)
    zonedata = flask.request.json
    if 'bypassed' in zonedata:
        want_bypass = zonedata['bypassed']
        if want_bypass == zone.bypassed:
            flask.abort(409)
        pin = flask.request.args.get('pin')
        partition = flask.request.args.get('partition', type=int)
        if _controller and _controller.protocol_needs_partition_for_bypass():
            if partition:
                if not pin:
                    return flask.jsonify({'error': 'PIN required when specifying partition'}), 400
                _controller.zone_bypass_toggle(zone.number, pin=pin, partition=partition)
            else:
                _controller.zone_bypass_toggle(zone.number)
        else:
            _controller.zone_bypass_toggle(zone.number)
    result = json.dumps(show_zone(zone))
    return flask.Response(result,
                          mimetype='application/json')


@app.route('/users/<int:user>')
def get_user(user):
    if not _check_protocol_support('users'):
        return flask.jsonify({'error': 'Protocol does not support user code management'}), 501
    args = flask.request.args
    master_pin = flask.request.headers.get('Master-Pin')
    if not master_pin:
        return 'Master PIN required', 403
    if user not in _controller.users:
        if 'retry' not in args:
            _controller.get_user_info(master_pin, user)
            return '', 202
        else:
            return 'Not Found', 404

    user = _controller.users[user]
    result = json.dumps(show_user(user))
    return flask.Response(result,
                          mimetype='application/json')


@app.route('/users/<int:user>', methods=['PUT'])
def put_user(user):
    if not _check_protocol_support('users'):
        return flask.jsonify({'error': 'Protocol does not support user code management'}), 501
    if user == 1:
        return 'I refuse to let you break your master user', 403
    master_pin = flask.request.headers.get('Master-Pin')
    if not master_pin:
        return 'Master PIN required', 403
    if user not in _controller.users:
        _controller.get_user_info(master_pin, user)
        return '', 204

    user = _controller.users[user]
    if 'master' in ''.join(user.authority_flags).lower():
        return 'I refuse to let you break a master user', 403

    userdata = flask.request.json
    changed = []
    if 'pin' in userdata:
        pin = userdata['pin']
        changed.append('pin')
        if pin is None:
            user.pin = [15] * 6
        elif len(pin) == 4:
            user.pin = [int(i) for i in pin] + [15, 15]
        elif len(pin) == 6:
            user.pin = [int(i) for i in pin]
        else:
            return 'Invalid PIN format', 400

    if changed:
        _controller.set_user_info(master_pin, user, changed)

    return flask.Response(json.dumps(show_user(user)),
                          mimetype='application/json')


@app.route('/events')
def get_events():
    index = int(flask.request.args.get('index', 0))
    timeout = int(flask.request.args.get('timeout', 10))
    events = _controller.event_queue.get(index, timeout=timeout)
    if events:
        index = events[-1].number
        events = [event.payload for event in events]
    return flask.Response(json.dumps({'events': events,
                                      'index': index}),
                          mimetype='application/json')


@app.route('/version')
def get_version():
    result = {
        'version': '1.2',
        'last_active': int(_controller.last_active),
    }
    if _controller:
        result['protocol'] = 'nx587' if _controller.use_nx587_device else 'nx584'
        result['features'] = _controller.protocol_features
    return flask.Response(json.dumps(result), mimetype='application/json')
