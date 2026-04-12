# ТехноМаркет — интернет-магазин бытовой техники
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
from functools import wraps
import logging
import base64
from io import BytesIO

app = Flask(__name__)
app.secret_key = 'secret_key_2026'
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# БД - для Vercel используем PostgreSQL или SQLite
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

if DATABASE_URL:
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///shop.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Пароль админа
ADMIN_PASS = 'admin123'

# --- Модели ---
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(100))
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    description = db.Column(db.Text)
    image_base64 = db.Column(db.Text)  # Храним картинку в base64

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_phone = db.Column(db.String(50))
    customer_address = db.Column(db.Text)
    total = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default='new')
    date = db.Column(db.DateTime, default=datetime.utcnow)

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'))
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product_name = db.Column(db.String(200))
    price = db.Column(db.Float)
    quantity = db.Column(db.Integer, default=1)

# --- Утилиты ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Сначала войдите в админку', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif', 'webp'}

# --- Страницы сайта ---
@app.route('/')
def index():
    products = Product.query.limit(12).all()
    return render_template('index.html', products=products)

@app.route('/products')
def products_page():
    search = request.args.get('search', '')
    cat = request.args.get('category', '')
    
    query = Product.query
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    if cat:
        query = query.filter_by(category=cat)
    
    products = query.all()
    categories = db.session.query(Product.category).distinct().all()
    categories = [c[0] for c in categories if c[0]]
    
    return render_template('products.html', products=products, categories=categories, 
                          search=search, current_category=cat)

@app.route('/product/<int:id>')
def product_detail(id):
    product = Product.query.get_or_404(id)
    return render_template('product_detail.html', product=product)

@app.route('/cart')
def cart():
    return render_template('cart.html')

@app.route('/checkout', methods=['POST'])
def checkout():
    try:
        data = request.json
        if not data or not data.get('items'):
            return jsonify({'error': 'Корзина пуста'}), 400
        
        name = data.get('name', '').strip()
        phone = data.get('phone', '').strip()
        address = data.get('address', '').strip()
        
        if not name or not phone or not address:
            return jsonify({'error': 'Заполните все поля'}), 400
        
        total = sum(item['price'] * item['quantity'] for item in data['items'])
        
        order = Order(
            customer_name=name,
            customer_phone=phone,
            customer_address=address,
            total=total
        )
        db.session.add(order)
        db.session.flush()
        
        for item in data['items']:
            order_item = OrderItem(
                order_id=order.id,
                product_id=item['id'],
                product_name=item['name'],
                price=item['price'],
                quantity=item['quantity']
            )
            db.session.add(order_item)
            
            # Обновляем остаток
            product = Product.query.get(item['id'])
            if product:
                product.stock = max(0, product.stock - item['quantity'])
        
        db.session.commit()
        return jsonify({'success': True, 'order_id': order.id})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Checkout error: {e}")
        return jsonify({'error': 'Ошибка оформления заказа'}), 500

# --- Админка ---
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASS:
            session['admin_logged_in'] = True
            flash('Добро пожаловать в админ-панель', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Неверный пароль', 'error')
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    flash('Вы вышли из админ-панели', 'success')
    return redirect(url_for('index'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect(url_for('admin_login'))
    
    products = Product.query.all()
    orders = Order.query.order_by(Order.date.desc()).all()
    return render_template('admin.html', products=products, orders=orders)

@app.route('/admin/product/add', methods=['GET', 'POST'])
@login_required
def admin_product_add():
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            category = request.form.get('category', '')
            price = float(request.form.get('price', 0))
            stock = int(request.form.get('stock', 0))
            description = request.form.get('description', '')
            
            # Обработка картинки
            image_base64 = None
            file = request.files.get('image')
            if file and file.filename and allowed_file(file.filename):
                data = file.read()
                image_base64 = base64.b64encode(data).decode('utf-8')
            
            product = Product(
                name=name,
                category=category,
                price=price,
                stock=stock,
                description=description,
                image_base64=image_base64
            )
            db.session.add(product)
            db.session.commit()
            flash('Товар успешно добавлен', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {str(e)}', 'error')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('product_form.html', title='Добавить товар', product=None)

@app.route('/admin/product/edit/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_product_edit(id):
    product = Product.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            product.name = request.form.get('name')
            product.category = request.form.get('category', '')
            product.price = float(request.form.get('price', 0))
            product.stock = int(request.form.get('stock', 0))
            product.description = request.form.get('description', '')
            
            # Обработка новой картинки
            file = request.files.get('image')
            if file and file.filename and allowed_file(file.filename):
                data = file.read()
                product.image_base64 = base64.b64encode(data).decode('utf-8')
            
            db.session.commit()
            flash('Товар успешно обновлен', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Ошибка: {str(e)}', 'error')
        return redirect(url_for('admin_dashboard'))
    
    return render_template('product_form.html', title='Редактировать товар', product=product)

@app.route('/admin/product/delete/<int:id>')
@login_required
def admin_product_delete(id):
    try:
        product = Product.query.get_or_404(id)
        db.session.delete(product)
        db.session.commit()
        flash('Товар удален', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {str(e)}', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/order/update/<int:id>', methods=['POST'])
@login_required
def admin_order_update(id):
    try:
        order = Order.query.get_or_404(id)
        order.status = request.form.get('status')
        db.session.commit()
        flash('Статус заказа обновлен', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {str(e)}', 'error')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/order/delete/<int:id>')
@login_required
def admin_order_delete(id):
    try:
        order = Order.query.get_or_404(id)
        db.session.delete(order)
        db.session.commit()
        flash('Заказ удален', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка: {str(e)}', 'error')
    return redirect(url_for('admin_dashboard'))

# --- Вспомогательные маршруты ---
@app.route('/product/image/<int:id>')
def product_image(id):
    product = Product.query.get_or_404(id)
    if product.image_base64:
        image_data = base64.b64decode(product.image_base64)
        from flask import Response
        return Response(image_data, mimetype='image/jpeg')
    return send_default_image()

def send_default_image():
    from flask import send_file
    return send_file('static/default.png', mimetype='image/png')

# --- Инициализация БД ---
with app.app_context():
    db.create_all()
    
    # Добавляем тестовые товары если пусто
    if Product.query.count() == 0:
        test_products = [
            Product(name="Холодильник Samsung RT-35", category="Холодильники", price=45990, stock=10, 
                   description="Энергоэффективный холодильник с системой No Frost"),
            Product(name="Стиральная машина LG F2J3HS0W", category="Стиральные", price=34990, stock=15,
                   description="Стиральная машина с прямым приводом и паром"),
            Product(name="Микроволновая печь Panasonic NN-GT261", category="Микроволновки", price=12990, stock=20,
                   description="Микроволновка с грилем и автоприготовлением"),
            Product(name="Пылесос Philips FC9350", category="Пылесосы", price=18990, stock=8,
                   description="Мощный пылесос с аквафильтром"),
            Product(name="Телевизор Samsung 55\"", category="Телевизоры", price=65990, stock=5,
                   description="4K UHD телевизор с HDR"),
            Product(name="Утюг Tefal FV9785", category="Утюги", price=5990, stock=25,
                   description="Паровой утюг с керамической подошвой"),
        ]
        for p in test_products:
            db.session.add(p)
        db.session.commit()
        print("Добавлены тестовые товары")

# Для Vercel
app = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
