#!/usr/bin/env python

import datetime
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import redis

import config


def setup_worker():
    # register as a worker
    workers = r.get('workers')
    if workers is None:
        r.set('workers', 0)
    else:
        print "found {0} workers".format(workers)
        if int(workers) >= config.workers:
            # we already have enough workers
            print "deactivating because we already have enough workers"
            sys.exit()
    r.incr('workers')


def signal_handler(signal, frame):
    print 'Shutting down worker'
    r.decr('workers')
    sys.exit(0)


def create_pr_env(work_item):
    print "working on {0}".format(work_item)
    org, project, pr = work_item.split('/')
    tempdir = tempfile.mkdtemp()
    subprocess.call(["git",
                                 "clone",
                                 "https://github.com/{0}/{1}".format(org,
                                                                     project),
                                 tempdir + "/job"])
    if pr != 'master':
        subprocess.Popen(["git",
                          "fetch",
                          "origin",
                          "pull/{0}/head:pr_{0}".format(pr)],
                         cwd=(tempdir + "/job")).communicate()
        subprocess.Popen(["git",
                          "checkout",
                          "pr_{0}".format(pr)],
                         cwd=(tempdir + "/job")).communicate()
    return str(tempdir)


def run_beaker_rspec(work_item, tempdir):
    # Record starttime
    t1 = datetime.datetime.utcnow()

    jobdir = tempdir + "/job"
    print "running in {0}".format(jobdir)

    # Setup environment
    runenv = os.environ.copy()
    runenv["BEAKER_set"] = 'libvirt'
    runenv["BEAKER_debug"] = 'yes'
    runenv["GEM_HOME"] = '/home/pcci/new_ruby_gems_home'
    runenv["PATH"] = '/home/pcci/new_ruby_gems_home/bin:' + runenv["PATH"]

    # Load environment variables from work item
    if work_item.get('environment') is not None:
        for k, v in work_item['environment'].items():
            runenv[k] = v
    print "Using libvirt nodeset: {0}".format(work_item['nodeset'])

    # Write out nodeset file
    shutil.copy('nodesets/' + work_item['nodeset'] + '.yml', jobdir + '/spec/acceptance/nodesets/libvirt.yml')

    # Run the test
    beaker = subprocess.Popen(["rspec", "spec/acceptance"],
                              cwd=jobdir,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              env=runenv)
    out, err = beaker.communicate()

    # Record endtime, calculate t delta
    t2 = datetime.datetime.utcnow()
    t_delta = t2 - t1

    # Setup response object
    response = {'out':               out,
                'err':               err,
                'success':           int(beaker.returncode),
                'time':              int(t_delta.seconds),
                'date':              str(datetime.datetime.utcnow()),
                'date_unix_seconds': datetime.datetime.utcnow().strftime('%s'),
                'nodeset':           work_item['nodeset'],
                'harness_failure': False,
                }

    # The most important metadata
    if response['success'] == 0:
        print "Tests passed"
        response['string_success'] = 'PASS'
    else:
        print "Tests failed"
        response['string_success'] = 'FAIL'

    return response


def write_log(work_item, response):
    path = config.logpath
    org, project, pr = work_item.split('/')
    if response['success'] == 0:
        succ = 'PASS'
    else:
        succ = 'FAIL'
    unix_seconds = datetime.datetime.utcnow().strftime('%s')
    filename = "{0}+{1}+{2}+{3}+{4}+{5}".format(org,
                                                project,
                                                pr,
                                                response['nodeset'],
                                                unix_seconds,
                                                succ)
    if response['harness_failure']:
        filename = "harness_failures/" + filename

    refilter = re.compile(r'\x1B\[[0-9;]*[a-zA-Z]')
    with open(path + "/" + filename, 'w') as f:
        f.write("Test log\n")
        f.write("Test performed at {0} - {1}\n".format(unix_seconds,
                                                       datetime.datetime.utcnow()))
        f.write("{0}/{1} PR # {2}\n".format(org, project, pr))
        f.write("Took {0} Seconds\n".format(response['time']))
        if response['success'] == 0:
            f.write("Tests passed\n")
        else:
            f.write("Tests failed\n")
        for line in response['out'].split('\n'):
            f.write(refilter.sub('', line))
            f.write('\n')
        for line in response['err'].split('\n'):
            f.write(refilter.sub('', line))
            f.write('\n')
    f.closed
    return (filename)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    r = redis.StrictRedis(host='localhost', port=6379, db=0)
    setup_worker()
    json_work_item = r.lpop('todo')
    if json_work_item is None:
        print "No work to do, shutting down"
        r.decr('workers')
        sys.exit()
    work_item = json.loads(json_work_item)
    print "starting work on {0}".format(work_item)
    job = json.loads(r.get(work_item['unique_name']))
    now = str(datetime.datetime.now())
    job['begin_test'] = now
    r.set(work_item['unique_name'], json.dumps(job))
    r.sadd("in_progress", work_item['unique_name'] + '-' + work_item['nodeset'])
    response = {}
    tempdir = create_pr_env(work_item['unique_name'])
    response = run_beaker_rspec(work_item, tempdir)

    # write log
    log_path = write_log(work_item['unique_name'], response)
    print "log written to {0}".format(log_path)

    # effect override
    if work_item.get('override_name') is not None:
        work_item['unique_name'] = work_item['override_name']

    # build test report object
    test = {}
    module_name = "/".join(work_item['unique_name'].split("/")[:-1])
    print "module name is {0}".format(module_name)
    test['unique_name'] = work_item['unique_name']
    test['nodeset'] = work_item['nodeset']
    test['module_name'] = module_name
    test['response'] = response
    test['pull'] = job
    test['ts'] = response['date_unix_seconds']
    test['success'] = response['success']
    test['log_path'] = log_path
    test['pr'] = work_item['unique_name'].split('/')[2]
    test['github_url'] = "https://github.com/{0}/{1}/pull/{2}".format(*work_item['unique_name'].split("/"))

    # record test results in redis
    r.rpush(module_name, json.dumps(test))
    r.rpush('results', json.dumps(test))

    # record test results in github
    # remove the giant text blobs before pushing on to the completed queue
    test['response']['out'] = None
    test['response']['err'] = None
    r.rpush('completed', json.dumps(test))

    # Cleanup
    r.srem("in_progress", work_item['unique_name'] + '-' + work_item['nodeset'])
    print 'Shutting down worker'
    r.decr('workers')
