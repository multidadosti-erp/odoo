define([
  'summernote/core/dom',
  'summernote/core/range'
], function (dom, range) {
  /**
   * @class module.VideoDialog
   *
   * VideoDialog
   */
  var VideoDialog = function () {
    var $dialog;
    var $videoUrl;
    var $videoBtn;

    this.initialize = function ($container, options) {
      $dialog = $container.find('.note-video-dialog');
      $videoUrl = $dialog.find('.note-video-url');
      $videoBtn = $dialog.find('.note-video-btn');
    };

    this.destroy = function () {
      if ($videoUrl) {
        $videoUrl.off('input keyup');
      }
      if ($videoBtn) {
        $videoBtn.off('click');
      }
      $dialog = null;
      $videoUrl = null;
      $videoBtn = null;
    };

    this.bindEnterKey = function ($input, $btn) {
      $input.on('keypress', function (e) {
        if (e.keyCode === 13) {
          $btn.trigger('click');
        }
      });
    };

    /**
     * Extract video ID from URL
     */
    var extractVideoInfo = function (url) {
      // YouTube
      var youtubeMatch = url.match(/(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/ ]{11})/i);
      if (youtubeMatch && youtubeMatch[1]) {
        return {
          provider: 'youtube',
          id: youtubeMatch[1],
          embedUrl: 'https://www.youtube.com/embed/' + youtubeMatch[1]
        };
      }

      // Vimeo
      var vimeoMatch = url.match(/vimeo\.com\/(?:channels\/(?:\w+\/)?|groups\/(?:[^\/]*)\/videos\/|album\/(?:\d+)\/video\/|video\/|)(\d+)(?:|\/\?)/);
      if (vimeoMatch && vimeoMatch[1]) {
        return {
          provider: 'vimeo',
          id: vimeoMatch[1],
          embedUrl: 'https://player.vimeo.com/video/' + vimeoMatch[1]
        };
      }

      // Dailymotion
      var dailymotionMatch = url.match(/dailymotion\.com\/(?:video|hub)\/([^_]+)/);
      if (dailymotionMatch && dailymotionMatch[1]) {
        return {
          provider: 'dailymotion',
          id: dailymotionMatch[1],
          embedUrl: 'https://www.dailymotion.com/embed/video/' + dailymotionMatch[1]
        };
      }

      return null;
    };

    /**
     * show video dialog
     * 
     * @param {Object} layoutInfo
     * @return {Promise}
     */
    this.showVideoDialog = function (layoutInfo) {
      var self = this;
      return $.Deferred(function (deferred) {
        var $editable = layoutInfo.editable();
        var $dialog = layoutInfo.dialog();
        var $videoDialog = $dialog.find('.note-video-dialog');
        var $videoUrl = $videoDialog.find('.note-video-url');
        var $videoBtn = $videoDialog.find('.note-video-btn');

        // Save current range
        var savedRange = range.create();

        // Reset input
        $videoUrl.val('');
        $videoBtn.addClass('disabled').prop('disabled', true);

        // Show dialog
        $videoDialog.modal('show');

        // Handle input changes
        $videoUrl.off('input keyup').on('input keyup', function () {
          var url = $videoUrl.val().trim();
          var videoInfo = extractVideoInfo(url);
          
          if (videoInfo) {
            $videoBtn.removeClass('disabled').prop('disabled', false);
          } else {
            $videoBtn.addClass('disabled').prop('disabled', true);
          }
        });

        // Handle enter key
        self.bindEnterKey($videoUrl, $videoBtn);

        // Handle video insertion
        $videoBtn.off('click').on('click', function (e) {
          e.preventDefault();
          
          var url = $videoUrl.val().trim();
          var videoInfo = extractVideoInfo(url);
          
          if (!videoInfo) {
            return;
          }

          // Restore range
          if (savedRange) {
            savedRange.select();
          }

          // Create responsive video embed
          var $videoWrapper = $('<div class="embed-responsive embed-responsive-16by9" style="max-width: 100%; margin: 10px 0;"></div>');
          var $iframe = $('<iframe class="embed-responsive-item" allowfullscreen></iframe>');
          $iframe.attr('src', videoInfo.embedUrl);
          $iframe.attr('frameborder', '0');
          $iframe.attr('allow', 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture');
          
          $videoWrapper.append($iframe);

          // Insert video
          savedRange.insertNode($videoWrapper[0]);

          // Move cursor after video
          var newRange = range.create();
          if (newRange) {
            newRange.setStartAfter($videoWrapper[0]);
            newRange.collapse(true);
            newRange.select();
          }

          // Close dialog
          $videoDialog.modal('hide');
          
          // Trigger change event
          $editable.trigger('change');
          
          deferred.resolve();
        });

        // Handle dialog close
        $videoDialog.on('hidden.bs.modal', function () {
          $videoUrl.off('input keyup');
          $videoBtn.off('click');
          $editable.focus();
          
          if (deferred.state() === 'pending') {
            deferred.reject();
          }
        });

        // Focus on input
        setTimeout(function() {
          $videoUrl.focus();
        }, 100);
      }).promise();
    };
  };

  return VideoDialog;
});
