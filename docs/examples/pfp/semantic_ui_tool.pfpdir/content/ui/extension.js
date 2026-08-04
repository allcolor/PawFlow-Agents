// Minimal package-owned semantic node.
pawflow.register('examples.semantic-ui', function (pfp) {
  var label = 'ready';

  pfp.semantic.register({
    id: 'demo.status',
    role: 'status',
    label: 'Semantic demo status',
    parent: 'conversation',
    state: function () {
      return { label: label };
    },
    actions: {
      setLabel: {
        parameters: {
          label: { type: 'string', required: true },
        },
        run: function (arguments) {
          label = arguments.label;
          return { label: label };
        },
      },
    },
  });
});
