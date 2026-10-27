/* Same-origin workbench client; no passwords or identity tokens are stored here. */
(function () {
  'use strict';
  const embedded=window.self!==window.top&&new URLSearchParams(location.search).get('embedded')==='adsight';
  const base = embedded?'/shoe-embed-api':'/shoe-api';
  const pathId = value => encodeURIComponent(value);
  async function request(path, options = {}) {
    const response = await fetch(base + path, {
      credentials: 'same-origin',
      ...options,
      headers: { ...(options.body && !(options.body instanceof Blob) ? { 'Content-Type': 'application/json' } : {}), ...options.headers }
    });
    if (!response.ok) {
      if(embedded&&response.status===401)window.parent.postMessage({type:'shoe:session-expired'},location.origin);
      let detail='';try{detail=(await response.json()).detail||'';}catch(e){}
      const error = new Error(typeof detail==='string'?detail:'请求未完成（'+response.status+'）');
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }
  const json = value => JSON.stringify(value);
  window.ShoeCloud = {
    base,
    helpArticles: () => request('/help/articles'),
    helpArticleRead: id => request('/help/articles/'+pathId(id)+'/read',{method:'POST'}),
    helpOnboardingSeen: () => request('/help/onboarding/seen',{method:'POST'}),
    saveHelpArticle: (id,value) => request('/admin/help/articles/'+pathId(id),{method:'PUT',body:json(value)}),
    rememberCurrent: () => request('/auth/accounts',{method:'POST'}),
    rememberedAccounts: () => request('/auth/accounts'),
    switchRemembered: id => request('/auth/accounts/'+pathId(id)+'/switch',{method:'POST'}),
    removeRemembered: id => request('/auth/accounts/'+pathId(id),{method:'DELETE'}),
    clearRemembered: () => request('/auth/accounts',{method:'DELETE'}),
    accountCredential: id => request('/admin/account-credentials/'+pathId(id)),
    login: (username, password, remember) => request('/auth/login', { method: 'POST', body: json({ username, password, remember }) }),
    logout: () => request('/auth/logout', { method: 'POST' }),
    me: () => request('/me'),
    points: () => request('/selling-points'),
    savePoint: (id, value) => request('/admin/selling-points/' + pathId(id), { method: 'PUT', body: json(value) }),
    archivePoint: id => request('/admin/selling-points/' + pathId(id) + '/archive', { method: 'POST' }),
    orderPoints: ids => request('/admin/selling-points-order', { method: 'PUT', body: json({ids}) }),
    orderTemplates: ids => request('/admin/templates-order', { method: 'PUT', body: json({ids}) }),
    templates: () => request('/templates'),
    adminTemplates: () => request('/admin/templates'),
    versions: id => request('/templates/' + pathId(id) + '/versions'),
    deleteVersion: (id, version) => request('/admin/templates/' + pathId(id) + '/versions/' + Number(version), { method: 'DELETE' }),
    draft: id => request('/admin/templates/' + pathId(id) + '/draft'),
    discardDraft: id => request('/admin/templates/'+pathId(id)+'/draft',{method:'DELETE'}),
    saveDraft: (id, value) => request('/admin/templates/' + pathId(id) + '/draft', { method: 'PUT', body: json(value) }),
    publish: (id, updateCurrent=false) => request('/admin/templates/' + pathId(id) + '/publish?update_current='+Boolean(updateCurrent), { method: 'POST' }),
    renameVersion: (id, version, name) => request('/admin/templates/'+pathId(id)+'/versions/'+Number(version), {method:'PATCH',body:json({name})}),
    resetUsage: (id, version) => request('/admin/templates/'+pathId(id)+'/usage/reset'+(version?'?version='+Number(version):''),{method:'POST'}),
    archiveTemplate: id => request('/admin/templates/' + pathId(id) + '/archive', { method: 'POST' }),
    preferences: () => request('/preferences'),
    savePreferences: value => request('/preferences', { method: 'PUT', body: json(value) }),
    feedback: (offset=0) => request('/feedback?offset='+Number(offset)),
    feedbackOne: id => request('/feedback/'+pathId(id)),
    resolveInlineMedia: (id,links,message_id=null) => request('/feedback/'+pathId(id)+'/inline-media',{method:'POST',body:json({links,message_id})}),
    createFeedback: value => request('/feedback', { method: 'POST', body: json(value) }),
    feedbackMessages: id => request('/feedback/' + pathId(id) + '/messages'),
    editReply: (id,message,content) => request('/feedback/'+pathId(id)+'/messages/'+pathId(message),{method:'PATCH',body:json({content})}),
    withdrawReply: (id,message) => request('/feedback/'+pathId(id)+'/messages/'+pathId(message)+'/withdraw',{method:'POST'}),
    restoreReply: (id,message) => request('/feedback/'+pathId(id)+'/messages/'+pathId(message)+'/restore',{method:'POST'}),
    deleteReply: (id,message) => request('/feedback/'+pathId(id)+'/messages/'+pathId(message),{method:'DELETE'}),
    editFeedback: (id, content) => request('/feedback/' + pathId(id), { method: 'PATCH', body: json({ content }) }),
    deleteFeedback: id => request('/feedback/' + pathId(id), { method: 'DELETE' }),
    reply: (id, content, reply_to = null, anonymous_to_users = false, template_id = null) => request('/feedback/' + pathId(id) + '/messages', { method: 'POST', body: json({ content, reply_to, anonymous_to_users, template_id }) }),
    notifyFeedbackAuthor: id => request('/feedback/' + pathId(id) + '/notify-author', {method:'POST'}),
    feedbackStatus: (id, status) => request('/feedback/' + pathId(id) + '/status', { method: 'PATCH', body: json({ status }) }),
    uploadFeedback: (id, blob, messageId) => request('/feedback/' + pathId(id) + '/attachments', { method: 'POST', headers: { 'Content-Type': blob.type, ...(messageId?{'X-Feedback-Message-Id':messageId}:{}) }, body: blob }),
    announcements: () => request('/announcements'),
    saveAnnouncement: (id, value) => request('/admin/announcements/' + pathId(id), { method: 'PUT', body: json(value) }),
    deleteAnnouncement: (id) => request('/admin/announcements/' + pathId(id), { method: 'DELETE' }),
    readAnnouncement: (id, method) => request('/announcements/' + pathId(id) + '/read', { method: 'POST', body: json({ method }) }),
    announcementReadSummary: id => request('/admin/announcements/'+pathId(id)+'/read-summary'),
    event: value => request('/events', { method: 'POST', body: json(value) }),
    eventSummary: () => request('/admin/events/summary'),
    usageDashboard: days => request('/admin/usage-dashboard?days='+Number(days)),
    suggestions: (id,version) => request('/templates/'+pathId(id)+'/suggestions?version='+Number(version)),
    userActivity: () => request('/admin/users/activity'),
    createAccount: value => request('/admin/accounts',{method:'POST',body:json(value)}),
    updateAccount: (id,value) => request('/admin/accounts/'+pathId(id),{method:'PATCH',body:json(value)}),
    resetAccount: id => request('/admin/accounts/'+pathId(id)+'/reset-password',{method:'POST'}),
    deleteAccount: id => request('/admin/accounts/'+pathId(id),{method:'DELETE'}),
    requestPasswordReset: value => request('/auth/reset-requests',{method:'POST',body:json(value)}),
    passwordRequests: () => request('/admin/reset-requests'),
    closePasswordRequest: id => request('/admin/reset-requests/'+pathId(id)+'/close',{method:'POST'}),
    createLocalAccount: value => request('/admin/local-accounts', {method:'POST', body:json(value)}),
    resetLocalAccount: id => request('/admin/local-accounts/'+pathId(id)+'/reset-password', {method:'POST'}),
    updateLocalAccount: (id,value) => request('/admin/local-accounts/'+pathId(id), {method:'PATCH',body:json(value)}),
    deleteLocalAccount: id => request('/admin/local-accounts/'+pathId(id), {method:'DELETE'}),
    userTemplateActivity: () => request('/admin/events/users'),
    help: () => request('/help'),
    saveHelp: value => request('/admin/help', { method: 'PUT', body: json(value) }),
    uploadHelp: blob => request('/admin/help/images', { method: 'POST', headers: { 'Content-Type': blob.type }, body: blob }),
    feedbackTypes: () => request('/feedback-types'),
    saveFeedbackTypes: value => request('/admin/feedback-types', { method: 'PUT', body: json(value) })
  };
})();
