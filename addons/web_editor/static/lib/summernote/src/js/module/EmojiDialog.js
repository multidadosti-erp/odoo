define([
  'summernote/core/dom',
  'summernote/core/range'
], function (dom, range) {
  /**
   * @class module.EmojiDialog
   *
   * EmojiDialog
   */
  var EmojiDialog = function () {
    var $dialog;
    var $emojiPicker;

    this.initialize = function ($container, options) {
      $dialog = $container.find('.note-emoji-dialog');
      $emojiPicker = $dialog.find('.note-emoji-picker');

      // Bind emoji button clicks
      $emojiPicker.on('click', '.note-emoji-btn', function (e) {
        e.preventDefault();
        var emoji = $(this).data('emoji');
        if (options && options.onInsertEmoji) {
          options.onInsertEmoji(emoji);
        }
      });
    };

    this.destroy = function () {
      if ($emojiPicker) {
        $emojiPicker.off('click', '.note-emoji-btn');
      }
      $dialog = null;
      $emojiPicker = null;
    };

    this.bindEnterKey = function ($input, $btn) {
      $input.on('keypress', function (e) {
        if (e.keyCode === 13) {
          $btn.trigger('click');
        }
      });
    };

    /**
     * show emoji dialog
     * 
     * @param {Object} layoutInfo
     * @return {Promise}
     */
    this.showEmojiDialog = function (layoutInfo) {
      var self = this;
      return $.Deferred(function (deferred) {
        var $editable = layoutInfo.editable();
        var $dialog = layoutInfo.dialog();
        var $emojiDialog = $dialog.find('.note-emoji-dialog');
        var $emojiPicker = $emojiDialog.find('.note-emoji-picker');

        // Save current range
        var savedRange = range.create();

        // Show dialog
        $emojiDialog.modal('show');

        // Handle emoji selection
        $emojiPicker.off('click').on('click', '.note-emoji-btn', function (e) {
          e.preventDefault();
          e.stopPropagation();
          
          var emoji = $(this).data('emoji');
          
          // Restore range and insert emoji
          if (savedRange) {
            savedRange.select();
          }
          
          // Insert emoji at cursor
          var $emoji = $('<span>' + emoji + '</span>');
          savedRange.insertNode($emoji[0]);
          
          // Move cursor after emoji
          var newRange = range.create();
          if (newRange) {
            newRange.setStartAfter($emoji[0]);
            newRange.collapse(true);
            newRange.select();
          }

          // Close dialog
          $emojiDialog.modal('hide');
          
          // Trigger change event
          $editable.trigger('change');
          
          deferred.resolve();
        });

        // Handle dialog close
        $emojiDialog.on('hidden.bs.modal', function () {
          $emojiPicker.off('click');
          $editable.focus();
          
          if (deferred.state() === 'pending') {
            deferred.reject();
          }
        });
      }).promise();
    };
  };

  return EmojiDialog;
});
