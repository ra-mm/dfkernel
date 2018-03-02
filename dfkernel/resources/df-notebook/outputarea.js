define(['jquery',
        'notebook/js/outputarea',
        'base/js/utils',
        'base/js/i18n',
], function($, outputarea, utils, i18n) {
    "use strict";

    var OutputArea = outputarea.OutputArea;

    OutputArea.output_prompt_function = function(prompt_value) {
        return $('<bdi>').text(i18n.msg.sprintf(i18n.msg._('Out[%s]:'),prompt_value));
    };

    // FIXME pull these in instead?
    // Declare mime type as constants
    var MIME_JAVASCRIPT = 'application/javascript';
    var MIME_HTML = 'text/html';
    var MIME_MARKDOWN = 'text/markdown';
    var MIME_LATEX = 'text/latex';
    var MIME_SVG = 'image/svg+xml';
    var MIME_PNG = 'image/png';
    var MIME_JPEG = 'image/jpeg';
    var MIME_PDF = 'application/pdf';
    var MIME_TEXT = 'text/plain';

    OutputArea.prototype.append_execute_result = function (json) {
        var n = json.execution_count || ' ';
        var toinsert = this.create_output_area();
        this._record_display_id(json, toinsert);
        if (this.prompt_area) {
            var p = toinsert.find('div.prompt')
                    .addClass('output_prompt')
                    .empty()
                    .append(
                      $('<bdi>').text('Out')
                    );
            if (json.metadata.output_tag) {
                p.append('[' + n + ']<br>.' + json.metadata.output_tag + ':');
            } else {
                p.append('[' + n + ']:');
            }
        }
        var inserted = this.append_mime_type(json, toinsert);
        if (inserted) {
            inserted.addClass('output_result');
        }
        this._safe_append(toinsert);
        // If we just output latex, typeset it.
        if ((json.data[MIME_LATEX] !== undefined) ||
            (json.data[MIME_HTML] !== undefined) ||
            (json.data[MIME_MARKDOWN] !== undefined)) {
            this.typeset();
        }
    };

});
