import json
from os import remove
from shutil import copyfile, move

import crossplane
from flask import Flask, render_template, request
from wtforms import Form, SubmitField, StringField, HiddenField, FieldList, FormField

from config import *

app = Flask(__name__)


# rx_sequence = re.compile(r"^application (\w+) {(.?)}", re.MULTILINE)

def divide_url(url: str) -> tuple:
    parts = url.split('/')
    if len(parts) > 3:
        return '/'.join(parts[:-1]), parts[-1]
    else:
        return url, ''


def make_url(url: str, stream_key: str):
    return url + '/' + stream_key


def run_command(command):
    import subprocess
    result = subprocess.Popen(command)
    text = result.communicate()[0]
    return result.returncode == 0


def get_config(app_name):
    c = crossplane.parse(VIDEO_FILE)
    rtmp_apps = c['config'][0]['parsed']
    apps = {}
    for rtmp_app in rtmp_apps:
        if rtmp_app['directive'] == 'application':
            name = rtmp_app['args'][0]
            if name == app_name:
                apps[name] = {'push': [], 'push_lines': []}

                for block in rtmp_app['block']:

                    if block['directive'] == 'allow' and block['args'][0] == 'play':
                        apps[name]['play_allow'] = block['args'][1]
                        apps[name]['play_line'] = block['line']

                    elif block['directive'] == 'allow' and block['args'][0] == 'publish':
                        apps[name]['publish_allow'] = block['args'][1]
                        apps[name]['publish_line'] = block['line']

                    elif block['directive'] == 'push':
                        rtmp_url = divide_url(block['args'][0])
                        apps[name]['push'].append({'url': rtmp_url[0], 'streamkey': rtmp_url[1], 'line': block['line']})
                        apps[name]['push_lines'].append(block['line'])

                    elif block['directive'] == 'access_log':
                        pass

                    # store app last line
                    apps[name]['last_line'] = block['line']

    return apps[app_name]


def update_lines(replaces: dict, appends: list, deletes: list, last_line: int):
    lines = open(VIDEO_FILE, 'r').readlines()
    print(lines)
    for num, content in replaces.items():
        lines[num - 1] = content + '\n'

    for content in appends:
        lines.insert(last_line, content + '\n')

    deletes.sort()
    removed = 0

    for line in deletes:
        lines.pop(line - 1 - removed)
        removed += 1

    out = open(VIDEO_FILE, 'w')
    out.writelines(lines)
    out.close()


def update_config(app_name, new_values):
    print(new_values)
    replaces = {}
    appends = []
    deletes = []

    if 'play_allow' in new_values:
        replaces[new_values['play_line']] = '\t\t\t\tallow play ' + new_values['play_allow'] + ';'

    if 'publish_allow' in new_values:
        replaces[new_values['publish_line']] = '\t\t\t\tallow publish ' + new_values['publish_allow'] + ';'

    if 'push' in new_values:
        for props in new_values['push']:

            if not props['url'] and not props['line']:  # skip empties
                continue
            elif not props['url'] and props['line']:  # remove lines
                deletes.append(int(props['line']))
            elif props['line'] and (int(props['line']) in new_values['push_lines']):
                url = make_url(props['url'], props['streamkey'])
                replaces[int(props['line'])] = '\t\t\t\t\t\tpush \'' + url + '\';'
            else:
                url = make_url(props['url'], props['streamkey'])
                appends.append('\t\t\t\t\t\tpush \'' + url + '\';')

    update_lines(replaces, appends, deletes, new_values['last_line'])
    print(replaces)
    print(appends)
    app.logger.info('Configuration changed: ' + json.dumps(replaces))


def backup_config():
    copyfile(VIDEO_FILE, VIDEO_FILE_BAK)


def apply_config():
    # test config
    if run_command(['sudo', '/usr/sbin/nginx', '-t']):
        if run_command(['sudo', '/usr/bin/systemctl', 'reload', 'nginx']):
            remove(VIDEO_FILE_BAK)
            return 'ok'
        else:
            return 'apply_fail'
    else:
        move(VIDEO_FILE_BAK, VIDEO_FILE)
        return 'test_fail'


class PushForm(Form):
    url = StringField('RTMP URL')
    streamkey = StringField('Stream klíč')
    line = HiddenField()


class ApplicationForm(Form):
    app_id = HiddenField()
    publish_allow = StringField('Povolit PUBLISH z', description='Zadej all pro všechny sítě nebo např. 172.20.1.0/24.')
    play_allow = StringField('Povolit PLAY z', description='Zadej all pro všechny sítě nebo např. 172.20.1.0/24.')
    button = SubmitField('Uložit')
    push = FieldList(FormField(PushForm), min_entries=4)


@app.route('/nginx-config/<app_name>', methods=['GET', 'POST'])
def index(app_name=None):
    result = None
    show_form = True
    form = None

    try:
        app_data = get_config(app_name)
    except KeyError:
        result = 'parse_app_failed'
        show_form = False

    if show_form:
        form = ApplicationForm(request.form) if request.method == 'POST' else ApplicationForm(data=app_data)
        if request.method == 'POST' and form.validate():
            app_data['publish_allow'] = form.publish_allow.data
            app_data['play_allow'] = form.play_allow.data
            app_data['push'] = form.push.data

            backup_config()
            try:
                update_config(app_name, app_data)
                result = apply_config()
            except PermissionError:
                result = 'write_perm_failed'

    return render_template('configuration.html', form=form, name=app_name,
                           result=result, show_form=show_form, config_file=VIDEO_FILE)


if __name__ == '__main__':
    app.run(debug=True)
