/**
 * Active tab navigation & page interactions
 * @param {string} defaultTab 
 */
function initTabNavigation(defaultTab) {
  // Read hash from URL to set active tab if present
  let activeTabId = defaultTab;
  if (window.location.hash) {
    const hash = window.location.hash.substring(1);
    if (document.getElementById(hash)) {
      activeTabId = hash;
    }
  }
  // Define tab switching function
  window.showTab = function(id, btnElement) {
    // Hide all sections
    document.querySelectorAll('.section').forEach(s => s.classList.remove('visible'));
    
    // Deactivate all buttons
    document.querySelectorAll('.topic-nav button').forEach(b => b.classList.remove('active'));
    
    // Show target section
    const targetSection = document.getElementById(id);
    if (targetSection) {
      targetSection.classList.add('visible');
    }
    
    // Activate target button
    if (btnElement) {
      btnElement.classList.add('active');
    } else {
      // Find button by onclick attribute if element was not passed directly
      const btn = document.querySelector(`.topic-nav button[onclick*="'${id}'"]`) || 
                  document.querySelector(`.topic-nav button[onclick*="${id}"]`);
      if (btn) btn.classList.add('active');
    }
    // Update URL hash without scrolling
    history.replaceState(null, null, '#' + id);
  };
  // Bind click handlers to navigation buttons
  document.querySelectorAll('.topic-nav button').forEach(btn => {
    const onClickStr = btn.getAttribute('onclick') || '';
    const match = onClickStr.match(/showTab\('([^']+)'/i) || onClickStr.match(/show\('([^']+)'/i);
    if (match && match[1]) {
      const tabId = match[1];
      btn.onclick = (e) => {
        e.preventDefault();
        window.showTab(tabId, btn);
      };
    }
  });
  // Activate initial tab
  window.showTab(activeTabId);
}

/**
 * Dynamic breadcrumb + back-link, depth-agnostic.
 * Runs automatically on any page containing #nav-id / #nav-back.
 * Overwrites whatever static fallback markup is in the HTML.
 */
function initBreadcrumb() {
  const navId = document.getElementById('nav-id');
  const navBack = document.getElementById('nav-back');
  if (!navId) return;

  const path = location.pathname.replace(/\/index\.html$/i, '/');
  const parts = path.split('/').filter(Boolean);

  navId.innerHTML = '';
  navId.appendChild(document.createTextNode('~/'));

  let acc = '';
  parts.forEach((part, i) => {
    acc += '/' + part;
    const isLast = i === parts.length - 1;
    if (isLast) {
      const b = document.createElement('b');
      b.textContent = part;
      navId.appendChild(b);
    } else {
      const a = document.createElement('a');
      a.href = acc + '/';
      a.style.color = 'inherit';
      a.style.textDecoration = 'none';
      a.textContent = part;
      navId.appendChild(a);
      navId.appendChild(document.createTextNode('/'));
    }
  });

  if (navBack) {
    navBack.href = parts.length <= 1 ? '/' : '/' + parts.slice(0, -1).join('/') + '/';
  }
}

document.addEventListener('DOMContentLoaded', initBreadcrumb);
