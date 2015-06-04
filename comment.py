
#from pdb import set_trace; set_trace()

import redis
from github import Github
import config
import time

# puppet-community-ci+test+1+1424905505+FAIL

def main_loop():
    while True:
        comment_to_make = r.lpop('completed')
        if comment_to_make is None:
            print "looping"
            time.sleep(5)
        else:
            comment(comment_to_make)


def comment(comment_to_make):
    org, project, pr, ts, success = comment_to_make.split('+')
    pr_object = g.get_repo(org + "/" + project).get_issue(int(pr))
    print "Considering {0}".format(comment_to_make)

    # Don't comment if not configured to
    if project in config.commentable:
        pass
    else:
        return

    # Don't recomment if status hasn't changed
    comments = [i for i in pr_object.get_comments() if i.user.login == 'puppet-community-ci']
    latest_comment = comments[-1]
    if success in latest_comment.body:
        print "Pass status unchanged, no need to comment"
        return

    print "Commenting on {0}".format(comment_to_make)

    response_string = """\
The result of the test was: {0}
Details at {1}{2}

I am a beta ci bot. I am probably lying to you.
You can contact nibalizer for more details."""\
        .format(success, config.rooturl, comment_to_make)

    pr_object.create_comment(response_string)


if __name__ == "__main__":

    g = Github("puppet-community-ci", config.password)
    r = redis.StrictRedis(host='localhost', port=6379, db=0)

    main_loop()





