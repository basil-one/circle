(function() {
  var measurementId = window.GA_MEASUREMENT_ID;
  if (!measurementId) return;

  window.dataLayer = window.dataLayer || [];
  function gtag(){ dataLayer.push(arguments); }
  window.gtag = window.gtag || gtag;

  gtag('js', new Date());
  gtag('config', measurementId, { anonymize_ip: true });

  document.addEventListener('DOMContentLoaded', function() {
    if (typeof window.gtag !== 'function') return;
    document.querySelectorAll('[data-ga-event]').forEach(function(el) {
      el.addEventListener('click', function(event) {
        var action = el.getAttribute('data-ga-event');
        var label = el.getAttribute('data-ga-label') || el.getAttribute('href') || 'unknown';
        var href = el.getAttribute('href');
        var target = el.getAttribute('target');

        var navigate = function() {
          if (!href) return;
          if (target === '_blank') {
            window.open(href, '_blank');
          } else {
            window.location = href;
          }
        };

        var hasDestination = href && href.charAt(0) !== '#';
        if (hasDestination) {
          event.preventDefault();
        }

        var eventSent = false;
        window.gtag('event', action, {
          event_category: 'engagement',
          event_label: label,
          transport_type: 'beacon',
          event_callback: function() {
            eventSent = true;
            navigate();
          }
        });

        if (hasDestination) {
          setTimeout(function() {
            if (!eventSent) navigate();
          }, 500);
        }
      });
    });
  });
})();
