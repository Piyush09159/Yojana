"""Shared progressive enhancement for backend and standalone public documents."""
def decorate(html, dashboard=False):
    if not dashboard:
        html = html.replace('The right support.', 'Find government schemes').replace('A clearer way forward.', 'you may qualify for.')
    script='dashboard-state.js' if dashboard else 'citizen.js'
    extra = '<link rel="stylesheet" href="/static/citizen.css"><script src="/static/'+script+'" defer></script>'
    if dashboard:
        extra += '<link rel="stylesheet" href="/static/dashboard-state.css">'
    return html.replace('</head>',extra+'</head>')
