from odoo import models, fields


class HrJobMixin(models.AbstractModel):
    _name = "hr.job.mixin"
    _description = "Mixin for HR Job-related fields"


    department_id = fields.Many2one(
        "hr.department",
        string="Department",
        help="Department of the employee")

    job_id = fields.Many2one(
        "hr.job",
        string="Job Position",
        help="Job position linked to the contract")

    job_title = fields.Char("Job Title")
