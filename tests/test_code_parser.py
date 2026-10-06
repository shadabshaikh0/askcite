from askcite.sources.code_parser import analyze_sql, parse_source

KOTLIN = '''
package demo.orders

class OrderRepository(private val jdbcTemplate: JdbcTemplate) {

    fun settledOrders(start: Timestamp, city: String?): List<Order> {
        val cityFilter = if (city != null) "and city = ?" else ""
        val query = """
            select o.order_id, o.amount
            from orders o
            where o.status = 'SETTLED'
              and o.settled_at >= ?
              $cityFilter
            order by o.settled_at
        """.trimIndent()
        return jdbcTemplate.query(query, arrayOf(start)) { rs, _ -> Order(rs) }
    }

    fun markPaid(orderId: Long) {
        jdbcTemplate.update("update orders set status = 'PAID' " +
            "where order_id = ?::bigint", orderId)
    }

    fun notSql(): String = "select your plan"
}
'''

JAVA = '''
package demo;
public class PaymentRepo {
    public int failed() {
        String q = "select count(*) from payments p " +
                   "join orders o on o.order_id = p.order_id where p.status = 'FAILED'";
        return jdbc.queryForObject(q, Integer.class);
    }
}
'''


def test_kotlin_functions_and_lines():
    parsed = parse_source("src/OrderRepository.kt", KOTLIN)
    names = {c.symbol: (c.start_line, c.end_line) for c in parsed.chunks}
    assert names["OrderRepository.settledOrders"] == (6, 17)
    assert "OrderRepository" in names and "OrderRepository.markPaid" in names


def test_kotlin_sql_is_rebuilt_from_pieces():
    parsed = parse_source("src/OrderRepository.kt", KOTLIN)
    by_symbol = {e.symbol: e for e in parsed.sql_examples}
    settled = by_symbol["OrderRepository.settledOrders"]
    assert settled.tables == ["orders"] and settled.operation == "select" and settled.parsed
    assert "/*cityFilter*/" in settled.sql_text and settled.sql_text.startswith("select o.order_id")
    paid = by_symbol["OrderRepository.markPaid"]
    assert paid.operation == "update" and paid.tables == ["orders"] and paid.parsed
    assert len(parsed.sql_examples) == 2  # "select your plan" is not SQL


def test_java_concatenated_sql():
    parsed = parse_source("src/PaymentRepo.java", JAVA)
    assert parsed.chunks[1].symbol == "PaymentRepo.failed"
    assert parsed.sql_examples[0].tables == ["orders", "payments"]


def test_unparseable_sql_still_finds_tables():
    operation, tables, parsed = analyze_sql("select * from orders o where /*dynamicWhere*/ and")
    assert tables == ["orders"]
