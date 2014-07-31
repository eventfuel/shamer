from github import Github
from flask import render_template
from jinja2 import TemplateNotFound
import requests

class GithubBot():
  def __init__(self, constants):
    self.g = Github(constants.get('GH_BOT_TOKEN'))
    self.user = self.g.get_user()
    self.org = self.g.get_organization(constants.get('GH_ORG_NAME'))
    self.repo = self.org.get_repo(constants.get('GH_REPO_NAME'))
    self.languages = constants.get('LANGS').split(',')
    self.constants = constants

  def past_comment(self, pr):
    for comment in pr.get_issue_comments():
      if comment.user.id == self.user.id:
        return comment

  def do_for_each_language(self, f):
    results = []
    for lang in self.languages:
      results.append(f(lang))
    return dict(zip(self.languages, results))

  def process_hook(self, pull_request_id, url, args, storage):
    message = self.constants.get('GH_BOT_MESSAGE')
    ci_restart_url = self.constants.get('CI_RESTART_URL')
    ci_api_key = self.constants.get('CI_API_KEY')
    build_id = args.get('build_id')
    if build_id:
      pr = self.repo.get_pull(pull_request_id)
      base_commit_sha = pr.base.sha
      if base_commit_sha not in storage.get('master'):
        base_commit_sha = sorted(storage.get('master').items(), key=lambda x: x[1]['build_id'])[0][0]
      # Sometimes coverage reports do funky things. This should prevent recording most of them.
      coverage_diffs = self.do_for_each_language(lambda l: float(args.get(l, 0)) - float(storage.get('master').get(base_commit_sha).get(l)))
      if min(coverage_diffs.values()) < -10:
        commit = self.repo.get_commit(args.get('commit_id')) or pr.get_commits().reversed[0]
        user = storage.get(commit.author.login) or {'name': commit.author.name, 'login': commit.author.login}
        if user.get('dangerously_low') != True:
          user['dangerously_low'] = True
          storage.set(pr.user.login, user)
          url = ci_restart_url.replace('$build_id$', build_id).replace('$api_key$', ci_api_key)
          requests.post(url)
          body = "Something doesn't look right... I'm re-running the coverage reports." \
            + "This comment will be updated when I'm done."
          self.post_comment(body, pr)
          return False
    self.update_leaderboard(pull_request_id, args, storage, coverage_diffs,)
    self.comment(pull_request_id, message, url, args, storage, coverage_diffs, base_commit_sha)
    return True

  def update_leaderboard(self, pull_request_id, args, storage, coverage_diffs):
    if storage.get('master'):
      pr = self.repo.get_pull(pull_request_id)
      user = storage.get(pr.user.login) or {'name': pr.user.name, 'login': pr.user.login}
      recorded = user.get('recorded', {})
      contribution = user.get('contribution', {lang:0 for lang in self.languages})
      pull_request_id = str(pr.id)
      if pull_request_id in recorded.keys():
        for lang in self.languages:
          contribution[lang] -= recorded[pull_request_id][lang]
      recorded[pull_request_id] = coverage_diffs
      for lang, coverage_diff in coverage_diffs.iteritems():
        contribution[lang] += coverage_diff
      user['contribution'] = contribution
      user['recorded'] = recorded
      user['net_contribution'] = sum(contribution.values())
      user['dangerously_low'] = False
      storage.set(pr.user.login, user)

  def comment(self, pull_request_id, message, url, args, storage, coverage_diffs, base_commit_sha):
    pr = self.repo.get_pull(pull_request_id)
    user = storage.get(pr.user.login)
    rank = None
    if user:
      all_users = [x['login'] for x in storage.all({'value.contribution': \
        {'$exists': True}}, ('value.net_contribution', -1))]
      rank = (all_users.index(pr.user.login) + 1, len(all_users))
    try:
      body = render_template('_comment.md', pr=pr, url=url, args=args, storage=storage, rank=rank, diffs=coverage_diffs, base=base_commit_sha)
    except TemplateNotFound:
      body = "{}: [{}]({})".format(message, pr.title, url)
    self.post_comment(body, pr)

  def post_comment(self, body, pr):
    past_comment = self.past_comment(pr)
    if past_comment:
      past_comment.edit(body)
    else:
      pr.create_issue_comment(body)

  def get_pr_by_branch(self, branch_name):
    for pull in self.repo.get_pulls(state='open'):
      if pull.head.ref == branch_name:
        return pull
