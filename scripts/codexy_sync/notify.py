"""Persistent conflict report and SMTP notification; never print credentials."""
from email.message import EmailMessage
import hashlib
import json
import os
from pathlib import Path
import smtplib
import ssl
import sys

from sync import api, markdown


def send_mail(subject, body, recipient):
    required = ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM')
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError('邮件未发送：缺少 GitHub Secrets ' + ', '.join(missing))
    message = EmailMessage()
    message['From'] = os.environ['SMTP_FROM']
    message['To'] = recipient
    message['Subject'] = subject
    message.set_content(body)
    port = int(os.environ['SMTP_PORT'])
    context = ssl.create_default_context()
    factory = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    options = {'context': context} if port == 465 else {}
    with factory(os.environ['SMTP_HOST'], port, timeout=30, **options) as client:
        if port != 465:
            client.starttls(context=context)
        client.login(os.environ['SMTP_USERNAME'], os.environ['SMTP_PASSWORD'])
        client.send_message(message)


def notify(report, repository):
    if report['status'] in ('noop', 'ready', 'validated', 'published'):
        return
    body = markdown(report)
    fingerprint = hashlib.sha256(body.encode()).hexdigest()
    title = f"[Codexy upstream] {report['tag']}: {report['status']}"
    # Search issues in this repository through paginated REST, including closed
    # issues: closing an issue never silently authorizes a conflicting update.
    issue = None
    for page in range(1, 101):
        issues = api(f'repos/{repository}/issues?state=all&per_page=100&page={page}')
        issue = next((item for item in issues if item['title'] == title and 'pull_request' not in item), None)
        if issue or len(issues) < 100:
            break
    else:
        raise RuntimeError('Issue scan limit exceeded; refusing duplicate notifications')
    marker = f'<!-- codexy-email-sent:{fingerprint} -->'
    sent = issue is not None and marker in (issue.get('body') or '')
    run_id = os.environ.get('GITHUB_RUN_ID')
    run_url = (f'https://github.com/{repository}/actions/runs/{run_id}' if run_id else
               f'https://github.com/{repository}/actions/workflows/codexy-upstream.yml')
    content = body[:55000] + f'\n\n运行日志：{run_url}\n完整文件列表及合并详情见 workflow artifact。\n'
    if sent:
        print('Conflict already reported and emailed; no duplicate mail.')
        return
    if issue is None:
        issue = api(f'repos/{repository}/issues', 'POST', {'title': title, 'body': content})
    else:
        issue = api(f"repos/{repository}/issues/{issue['number']}", 'PATCH', {'body': content})
    print(f"Report: {issue['html_url']}")
    recipient = json.loads(Path(__file__).with_name('policy.json').read_text())['recipient']
    send_mail(title, body + f"\n{issue['html_url']}\n{run_url}\n", recipient)
    api(f"repos/{repository}/issues/{issue['number']}", 'PATCH', {'body': content + '\n' + marker})
    print(f'Email accepted by SMTP server for {recipient}.')


if __name__ == '__main__':
    notify(json.loads(Path(sys.argv[1]).read_text()), os.environ['GITHUB_REPOSITORY'])
