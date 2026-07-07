from app.services.fee.scraper import _parse_fee_page, _row_to_fee_item


DATA_LABEL_FEE_TABLE_HTML = """
<div class="form-fee-body">
  <table>
    <tbody>
      <tr>
        <td data-label="Filing Category">
          <ol>
            <li>If you are filing an E-1, E-2, E-2C, or TN petition.</li>
            <li>If you are filing as a Small Employer or Nonprofit.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <ol>
            <li>$1,015 plus additional fees</li>
            <li>$510 plus additional fees, if applicable</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">
          <ol>
            <li>$965 plus additional fees</li>
            <li>$510 plus additional fees, if applicable</li>
          </ol>
        </td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <ol>
            <li>If you are filing an E-3 petition.</li>
            <li>If you are filing as a Small Employer or Nonprofit.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <ol>
            <li>$1,015 plus additional fees</li>
            <li>$510 plus additional fees, if applicable</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">N/A</td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <ol>
            <li>
              If you are filing an H-3 petition.<br>
              (<em>limited to 25 beneficiaries per petition</em>)
            </li>
            <li>If you are filing as a Small Employer or Nonprofit.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <ol>
            <li>$1,015 plus additional fees<br>&nbsp;</li>
            <li>$510 plus additional fees, if applicable</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">N/A</td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <ol>
            <li>
              If you are filing an O petition.<br>
              (<em>limited to 1 beneficiary per petition for O-1</em>;
              <em>limited to 25&nbsp;</em><br>
              <em>beneficiaries per petition for O-2</em>)
            </li>
            <li>If you are filing as a Small Employer or Nonprofit.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <ol>
            <li>$1,055 plus additional fees<br><br>&nbsp;</li>
            <li>$530 plus additional fees, if applicable</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">N/A</td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <ol>
            <li>
              If you are filing a P petition.<br>
              (<em>limited to 25 beneficiaries per petition</em>)
            </li>
            <li>If you are filing as a Small Employer or Nonprofit.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <ol>
            <li>$1,015 plus additional fees<br>&nbsp;</li>
            <li>$510 plus additional fees, if applicable</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">N/A</td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <ol>
            <li>
              If you are filing a Q petition.<br>
              (<em>limited to 25 beneficiaries per petition</em>)
            </li>
            <li>If you are filing as a Small Employer or Nonprofit.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <ol>
            <li>$1,015 plus additional fees<br>&nbsp;</li>
            <li>$510 plus additional fees, if applicable</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">N/A</td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <p class="indent-1">If you are filing an R petition.</p>
        </td>
        <td data-label="Paper Filing Fee">
          <p class="indent-1">$510 plus additional fees, if applicable</p>
        </td>
        <td data-label="Online Filing Fee">$510 plus additional fees, if applicable</td>
      </tr>
      <tr>
        <td data-label="Filing Category">
          <p>Additional Fees:</p>
          <ol class="ckeditor-list-type-attribute" style="list-style-type:lower-latin;" type="a">
            <li>Asylum Program Fee</li>
            <li>If you are filing as a Nonprofit;</li>
            <li>If you are filing as a Small Employer.</li>
          </ol>
        </td>
        <td data-label="Paper Filing Fee">
          <p>&nbsp;</p>
          <ol style="list-style-type:lower-latin;">
            <li>$600</li>
            <li>$0</li>
            <li>$300&nbsp;</li>
          </ol>
        </td>
        <td data-label="Online Filing Fee">
          <p>&nbsp;</p>
          <ol style="list-style-type:lower-latin;">
            <li>$600</li>
            <li>$0</li>
            <li>$300&nbsp;</li>
          </ol>
        </td>
      </tr>
    </tbody>
  </table>
</div>
"""


def test_parse_fee_page_reads_data_label_columns_and_ordered_list_rows():
    rows, form_url = _parse_fee_page(DATA_LABEL_FEE_TABLE_HTML)

    assert form_url is None
    assert len(rows) == 16
    assert rows[0] == {
        "Filing Category": "If you are filing an E-1, E-2, E-2C, or TN petition.",
        "Paper Filing Fee": "$1,015 plus additional fees",
        "Online Filing Fee": "$965 plus additional fees",
    }
    assert rows[3]["Filing Category"].startswith("If you are filing an E-3 petition.")
    assert rows[3]["Filing Category"].endswith("If you are filing as a Small Employer or Nonprofit.")
    assert rows[3]["Online Filing Fee"] == "N/A"
    assert rows[4]["Filing Category"] == (
        "If you are filing an H-3 petition. (limited to 25 beneficiaries per petition)"
    )

    fee_items = [
        _row_to_fee_item("I-129", "Petition for a Nonimmigrant Worker", None, row)
        for row in rows
    ]

    assert fee_items[0]["paper_fee"] == 1015.0
    assert fee_items[0]["online_fee"] == 965.0
    assert fee_items[3]["paper_fee"] == 510.0
    assert fee_items[3]["online_fee"] is None
    assert [item["paper_fee"] for item in fee_items[-3:]] == [600.0, 0.0, 300.0]
    assert [item["online_fee"] for item in fee_items[-3:]] == [600.0, 0.0, 300.0]
    assert fee_items[-3]["filing_category"] == "Additional Fees: Asylum Program Fee"
    assert fee_items[-2]["filing_category"] == "Additional Fees: If you are filing as a Nonprofit;"
    assert fee_items[-2]["filing_category"].endswith("If you are filing as a Nonprofit;")


def test_parse_fee_page_splits_numbered_fee_paragraphs():
    rows, _ = _parse_fee_page(
        """
        <div class="form-fee-body">
          <table>
            <thead>
              <tr>
                <th>Filing Category</th>
                <th>Paper Filing Fee</th>
                <th>Online Filing Fee</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <ol>
                    <li>If you are filing an H-2A petition with named workers.</li>
                    <li>If you are filing as a Small Employer or Nonprofit.</li>
                  </ol>
                </td>
                <td>
                  <ol>
                    <li>$1,090 plus additional fees</li>
                    <li>$545 plus additional fees, if applicable</li>
                  </ol>
                </td>
                <td>
                  <p>1. $1,040 plus additional fees</p>
                  <p><br>2. $545 plus additional fees, if applicable</p>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        """
    )

    assert len(rows) == 2
    assert rows[0]["Online Filing Fee"] == "$1,040 plus additional fees"
    assert rows[1]["Online Filing Fee"] == "$545 plus additional fees, if applicable"

    fee_items = [_row_to_fee_item("I-129", "Petition for a Nonimmigrant Worker", None, row) for row in rows]
    assert fee_items[0]["online_fee"] == 1040.0
    assert fee_items[1]["online_fee"] == 545.0


def test_parse_fee_page_does_not_split_explanatory_category_lists_without_fee_lists():
    rows, _ = _parse_fee_page(
        """
        <div class="form-fee-body">
          <table>
            <thead>
              <tr>
                <th>Filing Category</th>
                <th>Paper Filing Fee</th>
                <th>Online Filing Fee</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <p>Additional Fees:</p>
                  <p>H-1B petitioners must submit a Fraud Prevention and Detection fee if they are:</p>
                  <ol>
                    <li>Seeking initial approval of H-1B nonimmigrant status for a beneficiary; or</li>
                    <li>Seeking approval to employ an H-1B nonimmigrant currently working for another petitioner.</li>
                  </ol>
                  <p>Fraud Prevention and Detection fee, when applicable, may not be waived.</p>
                </td>
                <td>$500</td>
                <td>$500</td>
              </tr>
            </tbody>
          </table>
        </div>
        """
    )

    assert len(rows) == 1
    assert "Seeking initial approval" in rows[0]["Filing Category"]
    assert "Seeking approval to employ" in rows[0]["Filing Category"]
    assert "may not be waived" in rows[0]["Filing Category"]
    assert _row_to_fee_item("I-129", "Petition for a Nonimmigrant Worker", None, rows[0])[
        "paper_fee"
    ] == 500.0


def test_row_to_fee_item_keeps_missing_category_blank():
    item = _row_to_fee_item(
        "G-639",
        "Freedom of Information/Privacy Act Request",
        None,
        {"Paper Filing Fee": "$30", "Online Filing Fee": "$20"},
    )

    assert item["filing_category"] == ""
    assert item["paper_fee"] == 30.0
